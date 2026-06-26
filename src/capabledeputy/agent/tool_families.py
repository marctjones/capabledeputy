"""Load purpose-handle → tool family mappings for surface narrowing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.tools.registry import ToolDefinition

DEFAULT_TOOL_FAMILIES_PATH = Path("configs/tool_families.yaml")


@dataclass(frozen=True)
class ToolFamily:
    purpose_handles: frozenset[str] = field(default_factory=frozenset)
    include_kinds: frozenset[CapabilityKind] = field(default_factory=frozenset)
    include_prefixes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolFamiliesConfig:
    mandatory_always: tuple[str, ...]
    families: dict[str, ToolFamily]


def _kind_from_name(name: str) -> CapabilityKind | None:
    try:
        return CapabilityKind[name]
    except KeyError:
        return None


def load_tool_families(path: Path | None = None) -> ToolFamiliesConfig:
    config_path = path or DEFAULT_TOOL_FAMILIES_PATH
    if not config_path.is_file():
        return _builtin_defaults()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{config_path}: expected a YAML mapping")

    mandatory = tuple(str(n) for n in (raw.get("mandatory_always") or []))
    families: dict[str, ToolFamily] = {}
    for family_id, spec in (raw.get("families") or {}).items():
        if not isinstance(spec, dict):
            continue
        handles_raw = spec.get("purpose_handles") or []
        kinds: set[CapabilityKind] = set()
        for kind_name in spec.get("include_kinds") or []:
            parsed = _kind_from_name(str(kind_name))
            if parsed is not None:
                kinds.add(parsed)
        prefixes = tuple(str(p) for p in (spec.get("include_prefixes") or []))
        families[str(family_id)] = ToolFamily(
            purpose_handles=frozenset(str(h) for h in handles_raw),
            include_kinds=frozenset(kinds),
            include_prefixes=prefixes,
        )
    return ToolFamiliesConfig(mandatory_always=mandatory, families=families)


def _builtin_defaults() -> ToolFamiliesConfig:
    kinds_inbox = {
        CapabilityKind.GMAIL_READ,
        CapabilityKind.GMAIL_DRAFT,
        CapabilityKind.IMAP_READ,
    }
    return ToolFamiliesConfig(
        mandatory_always=("policy.preview",),
        families={
            "inbox": ToolFamily(
                purpose_handles=frozenset({"inbox"}),
                include_kinds=frozenset(kinds_inbox),
                include_prefixes=("inbox.", "email."),
            ),
            "calendar": ToolFamily(
                purpose_handles=frozenset({"calendar"}),
                include_kinds=frozenset(
                    {CapabilityKind.CALENDAR_READ, CapabilityKind.CALENDAR_WRITE},
                ),
                include_prefixes=("calendar.",),
            ),
            "general": ToolFamily(purpose_handles=frozenset({"general", "unset"})),
        },
    )


def family_for_purpose(config: ToolFamiliesConfig, purpose_handle: str) -> ToolFamily | None:
    for family in config.families.values():
        if purpose_handle in family.purpose_handles:
            return family
    return config.families.get("general")


def tool_matches_family(tool: ToolDefinition, family: ToolFamily) -> bool:
    if family.include_kinds and tool.capability_kind in family.include_kinds:
        return True
    if family.include_prefixes:
        return any(tool.name.startswith(prefix) for prefix in family.include_prefixes)
    if not family.include_kinds and not family.include_prefixes:
        return True
    return False