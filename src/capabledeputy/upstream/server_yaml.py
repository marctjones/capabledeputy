"""Per-server YAML config loader (Issue #35).

Each MCP server gets its own `~/.config/capabledeputy/servers.d/<name>.yaml`
containing connection details, custom permission kinds the server
introduces, and tool→kind mappings. This is the plugin distribution
unit: vendors ship one file with their server; operators drop it
into the directory; capdep picks it up at daemon startup.

The directory itself is the trust boundary. Anything inside
`servers.d/` is operator-authorized; capdep doesn't auto-discover
from package paths or remote URLs.

Design decisions locked in (per Issue #35 discussion):

1. **Namespace required**. Custom kinds MUST use `<namespace>:<path>`
   format (e.g., `slack:dm.send`). Built-in kinds (READ_FS,
   SEND_EMAIL, etc.) stay flat — they're capdep core.

2. **Refuse to load on collision**. If two server files declare the
   same kind name, the loader raises `KindCollisionError`. Silent
   shadowing is a security smell — operators must resolve.

3. **Override files**. Files matching `99-*.yaml` and declaring
   `overrides_server: <name>` patch an existing server's kinds /
   mappings without modifying the vendor's pristine file. Loaded
   after all non-override files (alphabetical sort).

4. **No package-path auto-discovery**. Only operator-curated files
   in `servers.d/` are loaded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.labels import LabelState, most_restrictive_inherit
from capabledeputy.upstream.config import (
    UpstreamServerConfig,
    UpstreamToolOverride,
    _parse_auth,
    _parse_isolation,
    _parse_label_state,
    expand_env_value,
)

# Custom kind name format: <namespace>:<dot-separated path>
# - namespace: at least one lowercase letter, alphanumeric + underscore allowed
# - path: alphanumeric + dot + underscore
# Built-in kinds (READ_FS, SEND_EMAIL, etc.) are uppercase and don't
# contain ':', so this regex deliberately mismatches them.
_CUSTOM_KIND_RE = re.compile(r"^[a-z][a-z0-9_]*:[a-z0-9_.]+$")


class ServerYamlError(Exception):
    """Base error for server-yaml loading."""


class InvalidKindNameError(ServerYamlError):
    """Custom kind name doesn't match the required namespace format."""


class KindCollisionError(ServerYamlError):
    """Two server files declared the same kind name."""


class UnknownOverrideTargetError(ServerYamlError):
    """An override file references a server that wasn't loaded."""


@dataclass(frozen=True)
class CustomKindDecl:
    """One kind declared in a server yaml file."""

    name: str  # MUST be namespaced (e.g. "slack:dm.send")
    description: str = ""
    destructive: bool = False
    pattern_anchor: str = ""  # "user_id", "channel_id", "path", etc.
    add_tags: LabelState = field(default_factory=LabelState)
    declared_by_file: str = ""  # filename for error messages

    @classmethod
    def from_dict(cls, raw: dict[str, Any], filename: str = "") -> CustomKindDecl:
        name = str(raw.get("name") or "")
        if not _CUSTOM_KIND_RE.match(name):
            raise InvalidKindNameError(
                f"{filename}: custom kind name {name!r} must use the format "
                "'<namespace>:<path>' (lowercase, e.g. 'slack:dm.send'). "
                "Built-in flat kinds like READ_FS are reserved for capdep core.",
            )
        tags_raw = raw.get("add_tags", raw.get("add_labels", {}))
        try:
            tags = _parse_label_state(tags_raw)
        except (ValueError, KeyError) as e:
            raise ServerYamlError(
                f"{filename}: kind {name!r} declares invalid tags: {e}",
            ) from e
        return cls(
            name=name,
            description=str(raw.get("description", "")),
            destructive=bool(raw.get("destructive", False)),
            pattern_anchor=str(raw.get("pattern_anchor", "")),
            add_tags=tags,
            declared_by_file=filename,
        )


@dataclass(frozen=True)
class ServerYamlConfig:
    """One server's complete config: connection + custom kinds +
    tool mappings + isolation. Maps to one yaml file in servers.d/."""

    name: str
    server_config: UpstreamServerConfig
    custom_kinds: tuple[CustomKindDecl, ...] = ()
    schema_version: int = 1
    source_file: str = ""  # for error messages

    @classmethod
    def from_dict(cls, raw: dict[str, Any], filename: str = "") -> ServerYamlConfig:
        schema_version = int(raw.get("schema_version", 1))
        if schema_version != 1:
            raise ServerYamlError(
                f"{filename}: schema_version {schema_version} not supported "
                f"(this capdep handles version 1)",
            )
        name = str(raw.get("name") or "")
        if not name:
            raise ServerYamlError(f"{filename}: missing required `name`")

        # Connection bits + isolation — reuse the existing config parsing.
        transport = str(raw.get("transport") or "stdio").lower().replace("-", "_")
        if transport == "http":
            transport = "streamable_http"
        if transport not in {"stdio", "streamable_http"}:
            raise ServerYamlError(f"{filename}: unsupported transport {transport!r}")
        command = tuple(str(a) for a in (raw.get("command") or []))
        url = str(raw.get("url") or raw.get("server_url") or raw.get("http_url") or "")
        if transport == "stdio" and not command:
            raise ServerYamlError(f"{filename}: stdio server requires `command`")
        if transport == "streamable_http" and not url:
            raise ServerYamlError(f"{filename}: HTTP server requires `url`")
        inherent_tags = _parse_label_state(
            raw.get("inherent_tags", raw.get("inherent_labels", {}))
        )
        env_raw = raw.get("env") or {}
        env = {str(k): expand_env_value(str(v)) for k, v in env_raw.items()}
        isolation = _parse_isolation(raw.get("isolation"))
        headers = {
            str(k): expand_env_value(str(v)) for k, v in (raw.get("headers") or {}).items()
        }
        auth = _parse_auth(raw.get("auth"))

        # Tool mappings — accept both the new short form (tool_mappings:
        # {tool_name: kind_string}) AND the legacy form (tool_overrides:
        # {tool_name: {capability_kind: ..., additional_tags: ...}}).
        tool_overrides: dict[str, UpstreamToolOverride] = {}

        # New short form
        mappings_raw = raw.get("tool_mappings") or {}
        for tool_name, kind_str in mappings_raw.items():
            tool_overrides[str(tool_name)] = UpstreamToolOverride(
                # capability_kind resolution defers — at this stage we
                # might be referencing a custom kind that hasn't been
                # registered yet. Stored as string; resolved by the
                # daemon's CustomKindRegistry consultation.
                capability_kind=None,
                additional_tags=LabelState(),
            )
            # Stash the raw kind-string on a side channel for resolution
            # at registration time. (See _resolve_tool_mappings.)
            # Hack until we generalize UpstreamToolOverride.
            tool_overrides[str(tool_name)] = _override_with_raw_kind(
                tool_overrides[str(tool_name)],
                str(kind_str),
            )

        # Legacy long form (for migration compatibility)
        overrides_raw = raw.get("tool_overrides") or {}
        for tool_name, ov in overrides_raw.items():
            kind_str = ov.get("capability_kind")
            kind = CapabilityKind(kind_str) if kind_str else None
            extra = _parse_label_state(ov.get("additional_tags", ov.get("additional_labels", {})))
            tool_overrides[str(tool_name)] = UpstreamToolOverride(
                capability_kind=kind,
                additional_tags=extra,
            )

        # Custom kinds
        kinds_raw = raw.get("kinds") or []
        custom_kinds = tuple(CustomKindDecl.from_dict(k, filename=filename) for k in kinds_raw)

        server_config = UpstreamServerConfig(
            name=name,
            command=command,
            transport=transport,
            url=url,
            headers=headers,
            auth=auth,
            inherent_tags=inherent_tags,
            tool_overrides=tool_overrides,
            isolation=isolation,
            env=env,
            strict=bool(raw.get("strict", True)),
            disabled_tools=frozenset(str(t) for t in (raw.get("disabled_tools") or [])),
            disabled_kinds=frozenset(str(k) for k in (raw.get("disabled_kinds") or [])),
        )

        return cls(
            name=name,
            server_config=server_config,
            custom_kinds=custom_kinds,
            schema_version=schema_version,
            source_file=filename,
        )


def _override_with_raw_kind(
    override: UpstreamToolOverride,
    raw_kind: str,
) -> UpstreamToolOverride:
    """Stash a raw kind-string on the override for later resolution
    against the CustomKindRegistry. Built-in kinds get resolved
    immediately; custom kinds resolve at registration time when
    the registry knows about them.
    """
    # Try resolving as built-in immediately
    try:
        resolved = CapabilityKind(raw_kind)
        return replace(override, capability_kind=resolved)
    except ValueError:
        pass
    # Custom kind — stash on an attribute the registry layer picks up.
    # We can't add to UpstreamToolOverride directly without breaking
    # back-compat, so the daemon resolution step looks up the raw
    # kind string from a side-channel dict (see CustomKindRegistry).
    return _OverrideWithCustomKind(
        capability_kind=None,
        additional_tags=override.additional_tags,
        _custom_kind_name=raw_kind,
    )


@dataclass(frozen=True)
class _OverrideWithCustomKind(UpstreamToolOverride):
    """Internal subclass that carries an unresolved custom-kind name.
    The daemon's CustomKindRegistry consults `_custom_kind_name` after
    all server yamls are loaded + kinds registered."""

    _custom_kind_name: str = ""


@dataclass(frozen=True)
class OverrideFile:
    """An override yaml (99-*.yaml) that patches an existing server's
    kinds / mappings."""

    overrides_server: str
    kinds: tuple[CustomKindDecl, ...] = ()
    tool_mappings: dict[str, str] = field(default_factory=dict)
    inherent_tags: LabelState = field(default_factory=LabelState)
    source_file: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any], filename: str = "") -> OverrideFile:
        target = str(raw.get("overrides_server") or "")
        if not target:
            raise ServerYamlError(
                f"{filename}: override file must declare `overrides_server: <name>`",
            )
        kinds_raw = raw.get("kinds") or []
        kinds = tuple(CustomKindDecl.from_dict(k, filename=filename) for k in kinds_raw)
        mappings = {str(k): str(v) for k, v in (raw.get("tool_mappings") or {}).items()}
        tags = _parse_label_state(raw.get("inherent_tags", raw.get("inherent_labels", {})))
        return cls(
            overrides_server=target,
            kinds=kinds,
            tool_mappings=mappings,
            inherent_tags=tags,
            source_file=filename,
        )


class CustomKindRegistry:
    """Runtime registry of custom kinds declared by server yamls.

    Kinds are registered at daemon startup (after all server yamls
    have loaded). Once registered, they can be resolved by name
    anywhere CapabilityKind is consulted: chokepoint matching,
    audit serialization, `/grant <KIND>` CLI dispatch, etc.

    The registry is process-global. Daemon restart re-reads yamls
    and rebuilds it. There's no persistence — yaml IS the source
    of truth.
    """

    def __init__(self) -> None:
        self._kinds: dict[str, CustomKindDecl] = {}

    def register(self, kind: CustomKindDecl) -> None:
        existing = self._kinds.get(kind.name)
        if existing is not None and existing.declared_by_file != kind.declared_by_file:
            raise KindCollisionError(
                f"Kind {kind.name!r} declared in both "
                f"{existing.declared_by_file} and {kind.declared_by_file}. "
                f"Each custom kind must be declared in exactly one file — "
                f"namespaces (e.g., {kind.name.split(':', 1)[0]}:*) prevent "
                f"collisions when used consistently.",
            )
        self._kinds[kind.name] = kind

    def get(self, name: str) -> CustomKindDecl | None:
        return self._kinds.get(name)

    def all(self) -> list[CustomKindDecl]:
        return sorted(self._kinds.values(), key=lambda k: k.name)

    def names(self) -> frozenset[str]:
        return frozenset(self._kinds.keys())

    def is_destructive(self, name: str) -> bool:
        decl = self._kinds.get(name)
        return decl is not None and decl.destructive


def load_servers_d(
    directory: Path,
) -> tuple[list[ServerYamlConfig], list[OverrideFile], CustomKindRegistry]:
    """Load all yaml files in `servers.d/`.

    Returns: (configs, overrides, kind_registry)

    Order: non-override files alphabetical, then override files
    (those whose name starts with `99-`). Override files can patch
    kinds and tool_mappings of an existing server config in-place.

    Raises:
        KindCollisionError: two files declared the same kind name
        UnknownOverrideTargetError: an override file references a
            server that wasn't loaded
        InvalidKindNameError: a custom kind name lacks namespace format
        ServerYamlError: schema_version / parsing problems
    """
    if not directory.is_dir():
        return [], [], CustomKindRegistry()

    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as e:
        raise RuntimeError(
            "PyYAML required for servers.d/ loading; install with `uv add pyyaml`",
        ) from e

    yaml_files = sorted(p for p in directory.glob("*.yaml") if p.is_file())
    yml_files = sorted(p for p in directory.glob("*.yml") if p.is_file())
    all_files = yaml_files + yml_files

    server_files = [p for p in all_files if not p.name.startswith("99-")]
    override_files = [p for p in all_files if p.name.startswith("99-")]

    configs: list[ServerYamlConfig] = []
    overrides: list[OverrideFile] = []
    registry = CustomKindRegistry()

    # First pass: load non-override files, register their kinds
    for path in server_files:
        text = path.read_text(encoding="utf-8")
        raw = yaml.safe_load(text) or {}
        if not isinstance(raw, dict):
            raise ServerYamlError(
                f"{path.name}: top-level must be a mapping (got {type(raw).__name__})",
            )
        if raw.get("overrides_server"):
            # Misplaced override file — shouldn't be in non-99- files
            raise ServerYamlError(
                f"{path.name}: declares overrides_server but filename "
                f"doesn't start with '99-'. Rename to 99-{path.name} "
                f"so it loads after vendor files.",
            )
        cfg = ServerYamlConfig.from_dict(raw, filename=path.name)
        # Register kinds — collisions raise immediately
        for kind in cfg.custom_kinds:
            registry.register(kind)
        configs.append(cfg)

    # Second pass: load override files
    server_names = {c.name for c in configs}
    for path in override_files:
        text = path.read_text(encoding="utf-8")
        raw = yaml.safe_load(text) or {}
        if not isinstance(raw, dict):
            raise ServerYamlError(
                f"{path.name}: top-level must be a mapping",
            )
        if not raw.get("overrides_server"):
            raise ServerYamlError(
                f"{path.name}: override file (99-*.yaml) must declare `overrides_server: <name>`",
            )
        ov = OverrideFile.from_dict(raw, filename=path.name)
        if ov.overrides_server not in server_names:
            raise UnknownOverrideTargetError(
                f"{path.name}: overrides_server: {ov.overrides_server!r} "
                f"but no server with that name was loaded. "
                f"Loaded servers: {sorted(server_names)}",
            )
        # Override-file kinds register too — but only if they don't
        # collide with an existing kind from the base server.
        # Pattern: override files can ADD new kinds or patch existing
        # ones; registering an override-kind under an existing name is
        # a patch, not a collision.
        for kind in ov.kinds:
            existing = registry.get(kind.name)
            if existing is None:
                registry.register(kind)
            # else: it's a patch; merging applied at config-resolution time
        overrides.append(ov)

    return configs, overrides, registry


def apply_overrides(
    configs: list[ServerYamlConfig],
    overrides: list[OverrideFile],
) -> list[ServerYamlConfig]:
    """Merge override files into their target server configs.

    Override semantics:
    - `kinds`: add or replace specific kinds by name
    - `tool_mappings`: add or override specific tool→kind mappings
    - `inherent_tags`: compose with (not replace) the server's tags
    """
    by_name = {c.name: c for c in configs}
    for ov in overrides:
        base = by_name[ov.overrides_server]
        # Merge kinds: override-supplied wins per name
        kind_by_name = {k.name: k for k in base.custom_kinds}
        for k in ov.kinds:
            kind_by_name[k.name] = k
        merged_kinds = tuple(sorted(kind_by_name.values(), key=lambda k: k.name))

        # Merge tool mappings
        merged_overrides = dict(base.server_config.tool_overrides)
        for tool_name, raw_kind in ov.tool_mappings.items():
            try:
                resolved = CapabilityKind(raw_kind)
                merged_overrides[tool_name] = UpstreamToolOverride(
                    capability_kind=resolved,
                    additional_tags=LabelState(),
                )
            except ValueError:
                merged_overrides[tool_name] = _OverrideWithCustomKind(
                    capability_kind=None,
                    additional_tags=LabelState(),
                    _custom_kind_name=raw_kind,
                )

        # Merge inherent tags (most-restrictive composition)
        merged_tags = most_restrictive_inherit(base.server_config.inherent_tags, ov.inherent_tags)

        merged_server = replace(
            base.server_config,
            tool_overrides=merged_overrides,
            inherent_tags=merged_tags,
        )
        by_name[ov.overrides_server] = replace(
            base,
            server_config=merged_server,
            custom_kinds=merged_kinds,
        )

    return list(by_name.values())
