"""YAML config schema for upstream MCP servers.

Each upstream server gets:
  - name: short identifier (used as a prefix on registered tool names)
  - command: argv list to launch the subprocess
  - inherent_labels: labels added to ANY tool result from this server
    (e.g., a fetch server gets `untrusted.external`)
  - tool_overrides: optional per-tool config (capability_kind override,
    additional inherent labels)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.labels import Label
from capabledeputy.upstream.isolation import ContainerIsolation, VolumeMount


@dataclass(frozen=True)
class UpstreamToolOverride:
    capability_kind: CapabilityKind | None = None
    additional_labels: frozenset[Label] = field(default_factory=frozenset)


@dataclass(frozen=True)
class UpstreamServerConfig:
    name: str
    command: tuple[str, ...]
    inherent_labels: frozenset[Label] = field(default_factory=frozenset)
    tool_overrides: dict[str, UpstreamToolOverride] = field(default_factory=dict)
    isolation: ContainerIsolation | None = None

    def effective_command(self) -> tuple[str, ...]:
        """If isolation is configured, prepend the container runtime's
        `run` argv so the upstream server actually launches inside the
        container. Otherwise the bare command runs directly."""
        if self.isolation is None:
            return self.command
        return self.isolation.to_argv_prefix() + self.command


def parse_config(raw: dict[str, Any]) -> list[UpstreamServerConfig]:
    servers_raw = raw.get("upstream_servers") or []
    out: list[UpstreamServerConfig] = []
    for entry in servers_raw:
        name = str(entry["name"])
        command = tuple(str(a) for a in entry["command"])
        inherent_labels = frozenset(Label(s) for s in entry.get("inherent_labels", []))
        overrides_raw = entry.get("tool_overrides", {}) or {}
        overrides: dict[str, UpstreamToolOverride] = {}
        for tool_name, ov in overrides_raw.items():
            kind_str = ov.get("capability_kind")
            kind = CapabilityKind(kind_str) if kind_str else None
            extra = frozenset(Label(s) for s in ov.get("additional_labels", []))
            overrides[tool_name] = UpstreamToolOverride(
                capability_kind=kind,
                additional_labels=extra,
            )
        isolation = _parse_isolation(entry.get("isolation"))
        out.append(
            UpstreamServerConfig(
                name=name,
                command=command,
                inherent_labels=inherent_labels,
                tool_overrides=overrides,
                isolation=isolation,
            ),
        )
    return out


def _parse_isolation(raw: dict[str, Any] | None) -> ContainerIsolation | None:
    if not raw:
        return None
    image = str(raw["image"])
    network = str(raw.get("network", "none"))
    if network not in ("none", "bridge", "host"):
        raise ValueError(f"invalid isolation.network: {network}")
    allowed_hosts = tuple(str(h) for h in raw.get("allowed_hosts", []) or [])
    volumes_raw = raw.get("volumes", []) or []
    volumes = tuple(
        VolumeMount(
            host=str(v["host"]),
            container=str(v["container"]),
            ro=bool(v.get("ro", True)),
        )
        for v in volumes_raw
    )
    env = {str(k): str(v) for k, v in (raw.get("env") or {}).items()}
    return ContainerIsolation(
        image=image,
        network=network,  # type: ignore[arg-type]
        allowed_hosts=allowed_hosts,
        volumes=volumes,
        memory=raw.get("memory"),
        cpus=raw.get("cpus"),
        env=env,
        user=str(raw.get("user", "1500:1500")),
        runtime=str(raw.get("runtime", "podman")),  # type: ignore[arg-type]
    )


def load_config_file(path: Path) -> list[UpstreamServerConfig]:
    import json

    text = path.read_text(encoding="utf-8")
    if path.suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as e:
            raise RuntimeError(
                "PyYAML is required for YAML configs; install with `uv add pyyaml`",
            ) from e
        raw = yaml.safe_load(text)
    else:
        raw = json.loads(text)
    return parse_config(raw or {})
