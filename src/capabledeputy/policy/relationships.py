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


@dataclass
class RelationshipGroups:
    """Operator-declared, mutable-via-RPC registry. The mutation
    surface (add_member, etc.) is the cookbook P2.3 auto-narrowing
    affordance — when the operator approves a send to a new
    counterparty, they can promote that identity to a group
    membership so future sends to the same counterparty resolve
    with a recognized counterparty rule.

    Mutation is operator-authorized only (via approval queue +
    explicit confirmation). The AI never invokes add_member /
    remove_member."""

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

    def resolve(self, principal_id: str) -> frozenset[str]:
        """Every group_id that `principal_id` belongs to. Empty when
        the identity isn't in any group. Used by the tool client to
        derive `axis_d.relationship_group_ids` at decide() time —
        the cookbook's family-personal-email-suggest rule depends
        on this resolution firing automatically for any send target."""
        return frozenset(
            g.group_id for g in self.groups.values() if principal_id in g.member_principal_ids
        )

    def add_member(self, group_id: str, principal_id: str) -> bool:
        """Add `principal_id` to `group_id`. Creates the group if it
        doesn't exist (cookbook P2.3: operators may invent new
        groups via approval-flow auto-narrowing). Returns True if
        the addition was new (membership changed), False if the
        principal was already a member.

        Operator authority only — invoked from the daemon's
        relationship_group.add_member RPC handler, never from
        tool code."""
        group = self.groups.get(group_id)
        if group is None:
            self.groups[group_id] = RelationshipGroup(
                group_id=group_id,
                member_principal_ids=frozenset({principal_id}),
            )
            return True
        if principal_id in group.member_principal_ids:
            return False
        self.groups[group_id] = RelationshipGroup(
            group_id=group_id,
            member_principal_ids=group.member_principal_ids | {principal_id},
        )
        return True

    def remove_member(self, group_id: str, principal_id: str) -> bool:
        """Remove `principal_id` from `group_id`. Returns True if the
        principal was present (membership changed), False otherwise.
        Empty groups are kept (an operator might be intentionally
        emptying a group temporarily)."""
        group = self.groups.get(group_id)
        if group is None:
            return False
        if principal_id not in group.member_principal_ids:
            return False
        self.groups[group_id] = RelationshipGroup(
            group_id=group_id,
            member_principal_ids=group.member_principal_ids - {principal_id},
        )
        return True


def save(groups: RelationshipGroups, path: Path) -> None:
    """Persist the registry back to YAML. Preserves the file's
    leading comment block by reading any existing `# ...` header
    lines from the current file and prepending them to the new
    body. The body itself is generated deterministically:

      groups:
        - group_id: family
          member_principal_ids:
            - spouse@example.com

    Sorted by group_id then by member id for stable diffs.

    Called by `relationship_group.add_member` (and friends) after
    an in-memory mutation so the change survives daemon restart.
    Failure to write surfaces as a RelationshipGroupError; the
    in-memory mutation is NOT rolled back, but the caller knows
    persistence didn't happen and can re-try."""
    header_lines: list[str] = []
    if path.is_file():
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            stripped = raw_line.strip()
            if stripped.startswith("#") or stripped == "":
                header_lines.append(raw_line)
            else:
                break
    body_parts: list[str] = []
    body_parts.append("groups:")
    if not groups.groups:
        # Replace the trailing colon with `groups: []` to keep YAML
        # round-trip-safe — yaml.safe_load of "groups:\n" yields
        # {"groups": None} which the loader treats as empty, but the
        # explicit empty list is clearer for the operator reading it.
        body_parts[-1] = "groups: []"
    else:
        for gid in sorted(groups.groups):
            g = groups.groups[gid]
            body_parts.append(f"  - group_id: {gid}")
            body_parts.append("    member_principal_ids:")
            for member in sorted(g.member_principal_ids):
                body_parts.append(f"      - {member}")
    new_text = "\n".join(header_lines + body_parts) + "\n"
    try:
        path.write_text(new_text, encoding="utf-8")
    except OSError as e:
        raise RelationshipGroupError(
            f"failed to persist relationship groups to {path}: {e}",
        ) from e


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
