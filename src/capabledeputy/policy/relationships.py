"""Relationship Group registry (003 US2 / FR-033).

Human-declared groupings of principals (e.g., `project-P`, `team-A`,
`spouse`). Operator-edited; AI-read-only. Used by decision rules to
scope sharing/disclosure decisions by relationship rather than by
identity alone.

Loaded from configs/relationship_groups.yaml. Empty registry is valid
(no groups declared yet); is_member() returns False for any principal
in that case.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


class RelationshipGroupError(RuntimeError):
    """The relationship groups config is malformed or unparseable.
    Fail-closed per Principle VI."""


@dataclass(frozen=True)
class RelationshipGroup:
    group_id: str
    member_principal_ids: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class RelationshipGroups:
    groups: dict[str, RelationshipGroup]

    def get(self, group_id: str) -> RelationshipGroup | None:
        return self.groups.get(group_id)

    def is_member(self, principal_id: str, group_id: str) -> bool:
        """True iff `principal_id` is in `group_id`. Unknown group ⇒
        False (fail-closed). Operator-declared, AI-read-only."""
        group = self.groups.get(group_id)
        if group is None:
            return False
        return principal_id in group.member_principal_ids


def load(path: Path) -> RelationshipGroups:
    """Load configs/relationship_groups.yaml. Missing file ⇒ error
    (Principle VI fail-closed). Empty `groups:` is permitted."""
    if not path.is_file():
        raise RelationshipGroupError(f"relationship groups config missing: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise RelationshipGroupError(f"unparseable: {path} — {e}") from e
    if data is None:
        return RelationshipGroups(groups={})
    raw = data.get("groups") or []
    if not isinstance(raw, list):
        raise RelationshipGroupError(f"'groups' must be a list: {path}")
    out: dict[str, RelationshipGroup] = {}
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise RelationshipGroupError(f"groups[{i}] is not an object")
        try:
            gid = str(item["group_id"])
        except KeyError:
            raise RelationshipGroupError(f"groups[{i}] missing 'group_id'") from None
        members = item.get("member_principal_ids") or []
        if not isinstance(members, list):
            raise RelationshipGroupError(f"groups[{i}].member_principal_ids must be a list")
        if gid in out:
            raise RelationshipGroupError(f"groups[{i}] duplicate group_id: {gid!r}")
        out[gid] = RelationshipGroup(
            group_id=gid,
            member_principal_ids=frozenset(str(m) for m in members),
        )
    return RelationshipGroups(groups=out)
