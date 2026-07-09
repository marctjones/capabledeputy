"""Demo 16 — per-tool container isolation.

Upstream MCP servers can run in podman containers with strict default
hardening: `--read-only`, `--cap-drop=ALL`, `--security-opt=
no-new-privileges`, `--user=1500:1500`, `--network=none` unless
explicitly opened. The runtime wraps the upstream command in the
generated argv prefix; MCP transport stays stdio.

Verified:
  - Strict defaults are emitted by `to_argv_prefix`.
  - `effective_command` correctly wraps the upstream command.
  - The quadlet generator produces a systemd-compatible unit with
    matching directives.
  - YAML parsing rejects invalid network modes.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from capabledeputy.upstream.config import (
    UpstreamServerConfig,
    load_config_file,
)
from capabledeputy.upstream.isolation import (
    ContainerIsolation,
    VolumeMount,
    quadlet_for,
)


def test_strict_defaults_block_network_and_writes() -> None:
    iso = ContainerIsolation(image="alpine")
    argv = iso.to_argv_prefix()
    # Network defaults to none; root FS read-only; capabilities dropped.
    assert "--network=none" in argv
    assert "--read-only" in argv
    assert "--cap-drop=ALL" in argv
    assert "--security-opt=no-new-privileges" in argv
    assert "--user=1500:1500" in argv


def test_volumes_default_to_read_only() -> None:
    iso = ContainerIsolation(
        image="alpine",
        volumes=(VolumeMount(host="/h", container="/c"),),
    )
    argv = iso.to_argv_prefix()
    # The default ro=True surfaces as a :ro suffix on the volume mount.
    assert "--volume=/h:/c:ro" in argv


def test_writable_volume_requires_explicit_opt_in() -> None:
    iso = ContainerIsolation(
        image="alpine",
        volumes=(VolumeMount(host="/h", container="/c", ro=False),),
    )
    argv = iso.to_argv_prefix()
    assert "--volume=/h:/c" in argv
    assert "--volume=/h:/c:ro" not in argv


def test_bridge_network_with_allowed_hosts_only() -> None:
    """When you DO need network, the host allowlist is the
    authorisation; everything else is blocked at podman's level."""
    iso = ContainerIsolation(
        image="alpine",
        network="bridge",
        allowed_hosts=("api.example.com:127.0.0.1",),
    )
    argv = iso.to_argv_prefix()
    assert "--network=bridge" in argv
    assert "--dns=none" in argv
    assert "--add-host=api.example.com:127.0.0.1" in argv


def test_effective_command_wraps_existing_invocation() -> None:
    iso = ContainerIsolation(image="alpine", network="none")
    cfg = UpstreamServerConfig(
        name="fs",
        command=("uvx", "mcp-server-filesystem", "/data"),
        isolation=iso,
    )
    eff = cfg.effective_command()
    assert eff[-3].endswith("/uvx") or eff[-3] == "uvx"
    assert eff[-2:] == ("mcp-server-filesystem", "/data")
    assert "--read-only" in eff
    # No isolation: command passes through unchanged.
    bare = UpstreamServerConfig(name="fs", command=("uvx", "mcp-server-filesystem"))
    bare_eff = bare.effective_command()
    assert bare_eff[0].endswith("/uvx") or bare_eff[0] == "uvx"
    assert bare_eff[1:] == ("mcp-server-filesystem",)


def test_quadlet_generator_emits_strict_directives(tmp_path: Path) -> None:
    iso = ContainerIsolation(
        image="docker.io/library/python:3.14-slim",
        network="none",
        volumes=(VolumeMount(host="/h", container="/c", ro=True),),
        memory="256m",
        cpus="0.5",
    )
    out = quadlet_for("fs", iso, ("uvx", "mcp-server-filesystem", "/c"))
    for fragment in (
        "Description=CapableDeputy upstream MCP server: fs",
        "Image=docker.io/library/python:3.14-slim",
        "Network=none",
        "ReadOnly=yes",
        "DropCapability=ALL",
        "NoNewPrivileges=yes",
        "Memory=256m",
        "Volume=/h:/c:ro",
    ):
        assert fragment in out


def test_yaml_round_trip_with_isolation(tmp_path: Path) -> None:
    """The config a user actually writes loads back with the strict
    defaults applied."""
    path = tmp_path / "upstream.yaml"
    path.write_text(
        textwrap.dedent(
            """\
            upstream_servers:
              - name: filesystem
                command: ["uvx", "mcp-server-filesystem", "/data"]
                inherent_labels: ["confidential.personal"]
                isolation:
                  image: docker.io/library/python:3.14-slim
                  network: none
                  volumes:
                    - host: /home/me/notes
                      container: /data
                      ro: true
                  memory: 256m
                  cpus: "0.5"
            """,
        ),
    )
    [cfg] = load_config_file(path)
    assert cfg.isolation is not None
    assert cfg.isolation.network == "none"
    assert cfg.isolation.volumes[0].ro is True
    eff = cfg.effective_command()
    assert "--network=none" in eff
    assert "--read-only" in eff


def test_yaml_invalid_network_rejected(tmp_path: Path) -> None:
    """Unknown network modes fail loudly at parse time."""
    import pytest as _p

    raw = {
        "upstream_servers": [
            {
                "name": "fs",
                "command": ["x"],
                "isolation": {"image": "alpine", "network": "weird"},
            },
        ],
    }
    from capabledeputy.upstream.config import parse_config

    with _p.raises(ValueError, match="network"):
        parse_config(raw)
