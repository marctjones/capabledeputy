"""Per-tool container isolation: argv prefix + quadlet generation."""

from __future__ import annotations

import textwrap

from capabledeputy.upstream.config import (
    UpstreamServerConfig,
    UpstreamToolOverride,
    parse_config,
)
from capabledeputy.upstream.isolation import (
    ContainerIsolation,
    VolumeMount,
    quadlet_for,
)


def test_argv_prefix_strict_defaults() -> None:
    iso = ContainerIsolation(image="docker.io/library/python:3.12-slim")
    argv = iso.to_argv_prefix()
    assert argv[0] == "podman"
    assert argv[1] == "run"
    assert "--rm" in argv
    assert "--read-only" in argv
    assert "--cap-drop=ALL" in argv
    assert "--security-opt=no-new-privileges" in argv
    assert "--network=none" in argv
    assert "--user=1500:1500" in argv
    assert argv[-1] == "docker.io/library/python:3.12-slim"


def test_argv_prefix_with_volumes_and_limits() -> None:
    iso = ContainerIsolation(
        image="alpine",
        network="bridge",
        allowed_hosts=("api.openai.com:127.0.0.1",),
        volumes=(VolumeMount(host="/h/data", container="/data", ro=True),),
        memory="256m",
        cpus="0.5",
        env={"FOO": "bar"},
    )
    argv = iso.to_argv_prefix()
    assert "--network=bridge" in argv
    assert "--add-host=api.openai.com:127.0.0.1" in argv
    assert "--volume=/h/data:/data:ro" in argv
    assert "--memory=256m" in argv
    assert "--cpus=0.5" in argv
    assert "--env=FOO=bar" in argv


def test_writable_volume_emits_no_ro_suffix() -> None:
    iso = ContainerIsolation(
        image="alpine",
        volumes=(VolumeMount(host="/h", container="/c", ro=False),),
    )
    argv = iso.to_argv_prefix()
    assert "--volume=/h:/c" in argv
    assert "--volume=/h:/c:ro" not in argv


def test_effective_command_wraps_when_isolation_set() -> None:
    iso = ContainerIsolation(image="alpine", network="none")
    cfg = UpstreamServerConfig(
        name="fs",
        command=("uvx", "mcp-server-filesystem", "/data"),
        isolation=iso,
    )
    eff = cfg.effective_command()
    # Isolation wraps the original command at the end:
    assert eff[-3:] == ("uvx", "mcp-server-filesystem", "/data")
    assert "--read-only" in eff


def test_effective_command_passthrough_when_no_isolation() -> None:
    cfg = UpstreamServerConfig(name="fs", command=("uvx", "mcp-server-filesystem", "/data"))
    assert cfg.effective_command() == ("uvx", "mcp-server-filesystem", "/data")


def test_parse_config_with_isolation() -> None:
    raw = {
        "upstream_servers": [
            {
                "name": "filesystem",
                "command": ["uvx", "mcp-server-filesystem", "/data"],
                "inherent_tags": {
                    "a": [
                        {
                            "category": "personal",
                            "tier": "regulated",
                            "risk_ids": [],
                            "assignment_provenance": "system-default",
                        }
                    ],
                    "b": [],
                },
                "isolation": {
                    "image": "docker.io/library/python:3.12-slim",
                    "network": "none",
                    "volumes": [
                        {"host": "/h/notes", "container": "/data", "ro": True},
                    ],
                    "memory": "256m",
                },
            },
        ],
    }
    [cfg] = parse_config(raw)
    assert cfg.isolation is not None
    assert cfg.isolation.image == "docker.io/library/python:3.12-slim"
    assert cfg.isolation.network == "none"
    assert cfg.isolation.memory == "256m"
    assert cfg.isolation.volumes[0].host == "/h/notes"
    # Check that the personal category tag is present
    assert any(tag.category == "personal" for tag in cfg.inherent_tags.a)


def test_parse_config_without_isolation_leaves_field_none() -> None:
    raw = {
        "upstream_servers": [
            {
                "name": "fetch",
                "command": ["uvx", "mcp-server-fetch"],
                "inherent_tags": {
                    "a": [],
                    "b": [{"level": "external-untrusted"}],
                },
                "tool_overrides": {
                    "fetch": {"capability_kind": "WEB_FETCH"},
                },
            },
        ],
    }
    [cfg] = parse_config(raw)
    assert cfg.isolation is None
    assert isinstance(cfg.tool_overrides["fetch"], UpstreamToolOverride)


def test_quadlet_renders_directives() -> None:
    iso = ContainerIsolation(
        image="docker.io/library/python:3.12-slim",
        network="none",
        volumes=(VolumeMount(host="/h", container="/c", ro=True),),
        memory="256m",
        cpus="0.5",
    )
    out = quadlet_for(
        "fs",
        iso,
        ("uvx", "mcp-server-filesystem", "/c"),
    )
    expected_fragments = [
        "Description=CapableDeputy upstream MCP server: fs",
        "Image=docker.io/library/python:3.12-slim",
        "Network=none",
        "ReadOnly=yes",
        "DropCapability=ALL",
        "NoNewPrivileges=yes",
        "Memory=256m",
        "Volume=/h:/c:ro",
        "Exec=uvx mcp-server-filesystem /c",
        "WantedBy=default.target",
    ]
    for frag in expected_fragments:
        assert frag in out, f"missing fragment: {frag}\n--- output ---\n{out}"


def test_parse_config_invalid_network_errors() -> None:
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
    with _p.raises(ValueError, match="network"):
        parse_config(raw)


def test_yaml_round_trip_with_isolation(tmp_path) -> None:
    """Isolation declared in YAML loads back with the right strict
    defaults applied."""
    from capabledeputy.upstream.config import load_config_file

    path = tmp_path / "upstream.yaml"
    path.write_text(
        textwrap.dedent(
            """\
            upstream_servers:
              - name: filesystem
                command: ["uvx", "mcp-server-filesystem", "/data"]
                inherent_tags:
                  a:
                    - category: personal
                      tier: regulated
                      risk_ids: []
                      assignment_provenance: system-default
                  b: []
                isolation:
                  image: docker.io/library/python:3.12-slim
                  network: none
                  volumes:
                    - host: /home/me/notes
                      container: /data
                      ro: true
                  memory: 256m
                  cpus: "0.5"
                  user: "1500:1500"
            """,
        ),
    )
    [cfg] = load_config_file(path)
    iso = cfg.isolation
    assert iso is not None
    assert iso.image.endswith("python:3.12-slim")
    assert iso.network == "none"
    assert iso.volumes[0].ro is True
