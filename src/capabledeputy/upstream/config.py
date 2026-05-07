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
        out.append(
            UpstreamServerConfig(
                name=name,
                command=command,
                inherent_labels=inherent_labels,
                tool_overrides=overrides,
            ),
        )
    return out


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
