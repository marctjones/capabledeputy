"""Pattern library: load reusable ApprovalPatternRule sets from YAML.

A pattern library is a YAML file with one or more named patterns. The
loader applies them to an `ApprovalPatternRegistry`, validating each
through `ApprovalPatternRule.create` so all the existing footgun
guards (no bare `*`, TTL ≤ 30 days, domain-anchored globs) still apply.

Library file shape:

    patterns:
      - name: spouse-prescription-emails
        action: SEND_EMAIL
        target_pattern: "spouse@example.com"
        payload_pattern: "*prescription*"   # optional
        ttl_hours: 24
        created_by: "library:family"        # optional, audit attribution

A pre-baked starter pack ships at `configs/approval-patterns.yaml`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from capabledeputy.approval.model import ApprovalAction
from capabledeputy.approval.pattern import (
    ApprovalPatternRegistry,
    ApprovalPatternRule,
    PatternValidationError,
)


class PatternLibraryError(ValueError):
    pass


@dataclass(frozen=True)
class LibraryEntry:
    name: str
    action: ApprovalAction
    target_pattern: str
    payload_pattern: str | None
    ttl_hours: float
    created_by: str = "library"


def parse_library(raw: dict[str, Any]) -> list[LibraryEntry]:
    patterns_raw = raw.get("patterns") or []
    if not isinstance(patterns_raw, list):
        raise PatternLibraryError("'patterns' must be a list")

    out: list[LibraryEntry] = []
    seen: set[str] = set()
    for entry in patterns_raw:
        if not isinstance(entry, dict):
            raise PatternLibraryError(f"pattern entry must be a mapping, got {type(entry)}")
        name = entry.get("name")
        if not name or not isinstance(name, str):
            raise PatternLibraryError("pattern entry missing 'name' string")
        if name in seen:
            raise PatternLibraryError(f"duplicate pattern name in library: {name}")
        seen.add(name)

        action_str = entry.get("action")
        if not action_str:
            raise PatternLibraryError(f"pattern '{name}' missing 'action'")
        try:
            action = ApprovalAction(action_str)
        except ValueError as e:
            raise PatternLibraryError(f"pattern '{name}' has unknown action: {action_str}") from e

        target = entry.get("target_pattern")
        if not target or not isinstance(target, str):
            raise PatternLibraryError(f"pattern '{name}' missing 'target_pattern'")

        ttl_hours_raw = entry.get("ttl_hours")
        if ttl_hours_raw is None:
            raise PatternLibraryError(f"pattern '{name}' missing 'ttl_hours'")
        try:
            ttl_hours = float(ttl_hours_raw)
        except (TypeError, ValueError) as e:
            raise PatternLibraryError(f"pattern '{name}' ttl_hours must be a number") from e

        payload_pattern = entry.get("payload_pattern")
        if payload_pattern is not None and not isinstance(payload_pattern, str):
            raise PatternLibraryError(f"pattern '{name}' payload_pattern must be a string")

        out.append(
            LibraryEntry(
                name=name,
                action=action,
                target_pattern=target,
                payload_pattern=payload_pattern,
                ttl_hours=ttl_hours,
                created_by=str(entry.get("created_by", f"library:{name}")),
            ),
        )
    return out


def load_library_file(path: Path) -> list[LibraryEntry]:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as e:
        raise PatternLibraryError(
            "PyYAML is required for pattern libraries; install with `uv add pyyaml`",
        ) from e

    text = path.read_text(encoding="utf-8")
    raw = yaml.safe_load(text) or {}
    if not isinstance(raw, dict):
        raise PatternLibraryError("library file must be a YAML mapping at top level")
    return parse_library(raw)


def apply_library(
    entries: list[LibraryEntry],
    registry: ApprovalPatternRegistry,
) -> list[ApprovalPatternRule]:
    """Validate every entry through `ApprovalPatternRule.create` and
    register it. If any entry fails the existing pattern footgun
    guards (bare `*`, TTL > 30 days, etc.), raise — partial loads are
    not supported because mixing approved and rejected entries from a
    library risks a silent partial-trust state.
    """
    rules: list[ApprovalPatternRule] = []
    for entry in entries:
        try:
            rule = ApprovalPatternRule.create(
                action=entry.action,
                target_pattern=entry.target_pattern,
                ttl=timedelta(hours=entry.ttl_hours),
                created_by=entry.created_by,
                payload_pattern=entry.payload_pattern,
            )
        except PatternValidationError as e:
            raise PatternLibraryError(
                f"pattern '{entry.name}' rejected by validator: {e}",
            ) from e
        registry.add(rule)
        rules.append(rule)
    return rules
