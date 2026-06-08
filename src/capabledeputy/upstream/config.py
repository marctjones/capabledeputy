"""YAML config schema for upstream MCP servers.

Each upstream server gets:
  - name: short identifier (used as a prefix on registered tool names)
  - command: argv list to launch the subprocess
  - env: optional per-server environment variables. Values can
    reference operator-shell env vars via ``${VAR}`` (or
    ``${VAR:-default}``) — expanded at config-load time. Enables the
    multi-credential pattern: two servers running the same upstream
    image with different credentials end up as distinct prefixed
    tools (e.g. ``github-work.list_issues`` vs.
    ``github-personal.list_issues``).
  - inherent_tags: LabelState tags added to ANY tool result from this server
    (e.g., a fetch server gets untrusted.external provenance)
  - tool_overrides: optional per-tool config (capability_kind override,
    additional inherent tags)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.labels import LabelState
from capabledeputy.upstream.isolation import ContainerIsolation, VolumeMount

_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def expand_env_value(value: str, environ: dict[str, str] | None = None) -> str:
    """Expand ``${VAR}`` / ``${VAR:-default}`` references in ``value``.

    Unknown vars without a default expand to empty string (matches POSIX
    shell behavior with ``set +u``). This is intentional: operator
    omitting an optional credential should not crash daemon startup.
    """
    env = environ if environ is not None else os.environ

    def _sub(match: re.Match[str]) -> str:
        var_name = match.group(1)
        default = match.group(2)
        return env.get(var_name, default if default is not None else "")

    return _ENV_VAR_PATTERN.sub(_sub, value)


@dataclass(frozen=True)
class UpstreamToolOverride:
    capability_kind: CapabilityKind | None = None
    additional_tags: LabelState = field(default_factory=LabelState)


@dataclass(frozen=True)
class UpstreamServerConfig:
    name: str
    command: tuple[str, ...]
    inherent_tags: LabelState = field(default_factory=LabelState)
    tool_overrides: dict[str, UpstreamToolOverride] = field(default_factory=dict)
    isolation: ContainerIsolation | None = None
    # Per-server environment variables, applied when spawning the
    # subprocess. Values are already expanded; ${VAR} references
    # resolved at parse_config time. Empty dict = inherit operator
    # shell env only.
    env: dict[str, str] = field(default_factory=dict)
    # Operator hard-disable list: tool names here are NEVER registered,
    # regardless of override or inference. Use to remove a capability the
    # upstream server exposes but the operator does not want available at
    # all (e.g. Gmail `send_gmail_message` to forbid outbound mail). The
    # adapter refuses these fail-closed before classification.
    disabled_tools: frozenset[str] = field(default_factory=frozenset)
    # Operator hard-disable by CAPABILITY KIND: any tool that resolves
    # (via override OR inference) to one of these kinds is refused —
    # name-independent. `disabled_kinds: {"SEND_EMAIL"}` forbids ALL
    # outbound mail from a server no matter what its send tool is called.
    # The robust "this server may not send email" control.
    disabled_kinds: frozenset[str] = field(default_factory=frozenset)
    # Fail-closed by default: an upstream tool that cannot be confidently
    # classified into a capability kind (no explicit override, no high-
    # confidence inference) is REFUSED registration rather than silently
    # granted a permissive default. Set strict=False only for trusted/
    # legacy servers where best-effort inference is acceptable.
    strict: bool = True

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
        inherent_tags = LabelState.from_dict(entry.get("inherent_tags", {}))
        overrides_raw = entry.get("tool_overrides", {}) or {}
        overrides: dict[str, UpstreamToolOverride] = {}
        for tool_name, ov in overrides_raw.items():
            kind_str = ov.get("capability_kind")
            kind = CapabilityKind(kind_str) if kind_str else None
            extra = LabelState.from_dict(ov.get("additional_tags", {}))
            overrides[tool_name] = UpstreamToolOverride(
                capability_kind=kind,
                additional_tags=extra,
            )
        isolation = _parse_isolation(entry.get("isolation"))
        env_raw = entry.get("env") or {}
        env = {str(k): expand_env_value(str(v)) for k, v in env_raw.items()}
        disabled_tools = frozenset(str(t) for t in (entry.get("disabled_tools") or []))
        disabled_kinds = frozenset(str(k) for k in (entry.get("disabled_kinds") or []))
        out.append(
            UpstreamServerConfig(
                name=name,
                command=command,
                inherent_tags=inherent_tags,
                tool_overrides=overrides,
                isolation=isolation,
                env=env,
                strict=bool(entry.get("strict", True)),
                disabled_tools=disabled_tools,
                disabled_kinds=disabled_kinds,
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
