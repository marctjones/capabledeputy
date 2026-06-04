"""Relationship Group registry (003 US2 / FR-033).

Human-declared groupings of principals (e.g., `project-P`, `team-A`,
`spouse`). Operator-edited; AI-read-only. Used by decision rules to
scope sharing/disclosure decisions by relationship rather than by
identity alone.

Loaded from configs/relationship_groups.yaml. Empty registry is valid
(no groups declared yet); is_member() returns False for any principal
in that case.

Roadmap v2 #4 — per-(group, principal) reputation tier. After N
approved sends to a counterparty, the operator can promote them
from `unproven` → `well-tested` → `trusted`. The chat REPL renders
approval cards differently per tier (full body for unproven,
subject + 200ch for well-tested, subject only for trusted),
reducing the operator-visible friction on safe-by-history
counterparties. Tier is operator-set only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

# Roadmap v2 #4 — three-tier reputation ladder. The order is
# meaningful: TIER_ORDER ranks them low → high so
# effective_tier_for() can pick the most-trusted membership when
# a principal is in multiple groups with differing tiers.
ReputationTier = Literal["unproven", "well-tested", "trusted"]
TIER_ORDER: tuple[ReputationTier, ...] = ("unproven", "well-tested", "trusted")
DEFAULT_TIER: ReputationTier = "unproven"


def _validate_tier(value: str) -> ReputationTier:
    if value not in TIER_ORDER:
        raise RelationshipGroupError(
            f"invalid reputation tier {value!r}; must be one of {TIER_ORDER}",
        )
    return value  # type: ignore[return-value]


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
    # Roadmap v2 #4 — per-(group_id, principal_id) reputation tier.
    # Keys not present resolve to DEFAULT_TIER ("unproven"). The
    # tier is independent of group membership; promote() requires
    # the principal to already be a member of the group. Operator-
    # set only — flipped via relationship_group.promote RPC.
    tiers: dict[tuple[str, str], ReputationTier] = field(default_factory=dict)

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

    def tier_for(self, group_id: str, principal_id: str) -> ReputationTier:
        """Return the reputation tier of `principal_id` within
        `group_id`. Unset → DEFAULT_TIER ("unproven"). Unknown
        group or non-member still returns DEFAULT_TIER — the
        caller decides whether membership matters."""
        return self.tiers.get((group_id, principal_id), DEFAULT_TIER)

    def effective_tier_for(self, principal_id: str) -> ReputationTier:
        """Highest tier across every group `principal_id` belongs
        to. When the recipient is in multiple groups, the operator
        already trusted them at each tier separately — taking the
        max means promotion in any group lightens the UX globally
        for that principal. Default ("unproven") when the
        principal isn't a member anywhere."""
        groups = self.resolve(principal_id)
        if not groups:
            return DEFAULT_TIER
        ranked = [self.tier_for(gid, principal_id) for gid in groups]
        return max(ranked, key=TIER_ORDER.index)

    def set_tier(
        self,
        group_id: str,
        principal_id: str,
        tier: str,
    ) -> ReputationTier:
        """Set (or change) the reputation tier of `principal_id`
        within `group_id`. Requires the principal to already be a
        member — promotion of a non-member is a programming error
        (the operator should add_member first). Returns the new
        tier; raises RelationshipGroupError on invalid tier value
        or non-membership."""
        validated = _validate_tier(tier)
        if not self.is_member(principal_id, group_id):
            raise RelationshipGroupError(
                f"cannot set tier for {principal_id!r} in group {group_id!r}: not a member",
            )
        if validated == DEFAULT_TIER:
            # Storing the default explicitly bloats the registry
            # without changing behavior — clear instead.
            self.tiers.pop((group_id, principal_id), None)
        else:
            self.tiers[(group_id, principal_id)] = validated
        return validated

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
        # Drop any tier override — the principal no longer has
        # standing in this group, so a lingering tier would
        # mislead a re-add later.
        self.tiers.pop((group_id, principal_id), None)
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
            # Roadmap v2 #4 — emit non-default tiers per member.
            # Members at DEFAULT_TIER are omitted to keep the file
            # readable for the operator (default-tier rows would
            # bloat it).
            tier_entries = sorted(
                (pid, t) for (g_id, pid), t in groups.tiers.items() if g_id == gid
            )
            if tier_entries:
                body_parts.append("    member_tiers:")
                for pid, t in tier_entries:
                    body_parts.append(f"      {pid}: {t}")
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
    tiers: dict[tuple[str, str], ReputationTier] = {}
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
        # Roadmap v2 #4 — optional member_tiers map. Validate each
        # tier string; bad values fail-closed per Principle VI.
        # Tiers for non-members are dropped at load (we can't
        # promote a non-member; keeping the tier would be a
        # phantom state).
        raw_tiers = item.get("member_tiers") or {}
        if not isinstance(raw_tiers, dict):
            raise RelationshipGroupError(
                f"groups[{i}].member_tiers must be a mapping",
            )
        member_set = frozenset(str(m) for m in members)
        for raw_pid, raw_tier in raw_tiers.items():
            pid = str(raw_pid)
            if pid not in member_set:
                continue
            tiers[(gid, pid)] = _validate_tier(str(raw_tier))
    return RelationshipGroups(groups=out, tiers=tiers)
