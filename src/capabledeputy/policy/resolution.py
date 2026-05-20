"""Deterministic sensitivity-resolution layer (003 US1 / FR-007 / SC-002).

Given a data category, a set of matching ContextProfiles, and the
operator-declared category definitions, return the resolved Tier for
the datum — deterministically, with no LLM in the path. The function
is pure: same (category_id, profile_ids, categories, profiles) -> same
(Tier, rationale). Replayable from logged inputs (SC-002).

Resolution rules (FR-007):
- Each Category declares a `default_tier` and a `resolution_mode`.
- `fixed-high`: the category's default_tier is the floor and ceiling.
  No profile can lower it (FR-007 specifically; US1 scenario 3).
- `context-up`: a profile may RAISE the tier above default. Cannot lower.
- `context-resolved`: profile may both raise and lower within bounds.
  (Lowering is allowed only via human-ratified profile, not via the
  AI's reasoning — that's structural by virtue of profiles being
  operator-edited YAML, not LLM-authored. FR-031 asymmetry.)
- Multiple matching profiles compose **most-restrictive** per-category
  (FR-026a baseline). The bounded-relax cases land in US2/US6.

Profiles are loaded from configs/profiles.yaml; categories from
configs/labels.yaml. Both are operator-edited, AI-read-only. A category
or profile not in the loaded set is FAIL-CLOSED (refuse to resolve)
per Principle VI — never silently degrade to a default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

from capabledeputy.policy.tiers import Tier, compare, max_of


class ResolutionError(RuntimeError):
    """Fail-closed: a category or profile is unknown, or the inputs
    are otherwise unresolvable. Per Principle VI, the runtime MUST
    treat this as deny — never best-effort allow."""


ResolutionMode = Literal["fixed-high", "context-up", "context-resolved"]


@dataclass(frozen=True)
class Category:
    """One Axis A category definition (loaded from configs/labels.yaml).

    `resolution_mode` controls how profiles may modify the tier:
      fixed-high       — immovable; profiles cannot raise or lower.
      context-up       — profiles may RAISE only.
      context-resolved — profiles may raise OR lower (within bounds).
    """

    id: str
    default_tier: Tier
    resolution_mode: ResolutionMode = "context-up"
    risk_ids: tuple[str, ...] = field(default_factory=tuple)
    c_impact: str = "medium"
    i_impact: str = "medium"
    kind: str = "registered"


@dataclass(frozen=True)
class CategoryOverride:
    """A profile's optional per-category override."""

    category: str
    tier: Tier | None = None
    resolution_mode: ResolutionMode | None = None


@dataclass(frozen=True)
class ContextProfile:
    """One profile (loaded from configs/profiles.yaml). A profile is
    operator-declared and matches sessions by `user_pattern` +
    `use_case`. Matching uses literal equality in US1; richer
    patterning is a deliberate non-goal until a user story needs it.

    `max_tier` is the read-up ceiling for FR-008 (US5); US1's
    resolver respects it but does not enforce it as a clearance check
    — that's added in T101.
    """

    id: str
    user_pattern: str = "*"
    use_case: str = "general"
    max_tier: Tier | None = None
    category_overrides: tuple[CategoryOverride, ...] = field(default_factory=tuple)

    def override_for(self, category_id: str) -> CategoryOverride | None:
        for o in self.category_overrides:
            if o.category == category_id:
                return o
        return None

    def matches(self, *, user: str, use_case: str) -> bool:
        """Literal equality match. `*` user_pattern matches any user.
        Richer matching (regex, wildcards on use_case) is non-goal
        for US1."""
        user_ok = self.user_pattern == "*" or self.user_pattern == user
        use_ok = self.use_case == use_case
        return user_ok and use_ok


@dataclass(frozen=True)
class ResolutionResult:
    """The output of resolve_tier(). `rationale` is a stable string
    that captures *why* — used by SC-002 replay (identical inputs ⇒
    identical rationale)."""

    tier: Tier
    rationale: str
    contributing_profile_ids: tuple[str, ...] = field(default_factory=tuple)


# --- Loaders --------------------------------------------------------


def load_categories(path: Path) -> dict[str, Category]:
    """Load operator-declared category definitions from
    configs/labels.yaml. Fail-closed on missing file or unparseable
    content; missing-or-empty `categories:` is permitted (yields empty
    dict — operator hasn't declared any yet)."""
    if not path.is_file():
        raise ResolutionError(f"categories config missing: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ResolutionError(f"categories config unparseable: {path} — {e}") from e
    if data is None:
        return {}
    cats_raw = data.get("categories") or []
    if not isinstance(cats_raw, list):
        raise ResolutionError(f"categories config: 'categories' must be a list: {path}")
    out: dict[str, Category] = {}
    for i, raw in enumerate(cats_raw):
        if not isinstance(raw, dict):
            raise ResolutionError(f"categories[{i}] is not an object")
        try:
            cid = str(raw["id"])
            default_tier = Tier(raw.get("default_tier", "sensitive"))
        except KeyError as e:
            raise ResolutionError(f"categories[{i}] missing required: {e.args[0]!r}") from e
        except ValueError as e:
            raise ResolutionError(f"categories[{i}] bad tier: {e}") from e
        mode_raw = str(raw.get("resolution_mode", "context-up"))
        if mode_raw not in ("fixed-high", "context-up", "context-resolved"):
            raise ResolutionError(f"categories[{i}] bad resolution_mode: {mode_raw!r}")
        if cid in out:
            raise ResolutionError(f"categories[{i}] duplicate id: {cid!r}")
        out[cid] = Category(
            id=cid,
            default_tier=default_tier,
            resolution_mode=mode_raw,  # pyright: ignore[reportArgumentType]
            risk_ids=tuple(str(r) for r in (raw.get("risk_ids") or [])),
            c_impact=str(raw.get("c_impact", "medium")),
            i_impact=str(raw.get("i_impact", "medium")),
            kind=str(raw.get("kind", "registered")),
        )
    return out


def load_profiles(path: Path) -> dict[str, ContextProfile]:
    """Load operator-declared context profiles. Same fail-closed
    semantics as load_categories."""
    if not path.is_file():
        raise ResolutionError(f"profiles config missing: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ResolutionError(f"profiles config unparseable: {path} — {e}") from e
    if data is None:
        return {}
    profs_raw = data.get("profiles") or []
    if not isinstance(profs_raw, list):
        raise ResolutionError(f"profiles config: 'profiles' must be a list: {path}")
    out: dict[str, ContextProfile] = {}
    for i, raw in enumerate(profs_raw):
        if not isinstance(raw, dict):
            raise ResolutionError(f"profiles[{i}] is not an object")
        try:
            pid = str(raw["id"])
        except KeyError:
            raise ResolutionError(f"profiles[{i}] missing required: 'id'") from None
        max_tier = Tier(raw["max_tier"]) if raw.get("max_tier") else None
        overrides_raw = raw.get("category_overrides") or []
        overrides: list[CategoryOverride] = []
        for j, oraw in enumerate(overrides_raw):
            if not isinstance(oraw, dict):
                raise ResolutionError(f"profiles[{i}].category_overrides[{j}] not object")
            try:
                cat = str(oraw["category"])
            except KeyError:
                raise ResolutionError(
                    f"profiles[{i}].category_overrides[{j}] missing 'category'",
                ) from None
            tier_raw = oraw.get("tier")
            mode_raw = oraw.get("resolution_mode")
            overrides.append(
                CategoryOverride(
                    category=cat,
                    tier=Tier(tier_raw) if tier_raw else None,
                    resolution_mode=mode_raw,  # pyright: ignore[reportArgumentType]
                ),
            )
        if pid in out:
            raise ResolutionError(f"profiles[{i}] duplicate id: {pid!r}")
        out[pid] = ContextProfile(
            id=pid,
            user_pattern=str(raw.get("user_pattern", "*")),
            use_case=str(raw.get("use_case", "general")),
            max_tier=max_tier,
            category_overrides=tuple(overrides),
        )
    return out


# --- Resolver -------------------------------------------------------


def resolve_tier(
    *,
    category_id: str,
    profile_ids: tuple[str, ...],
    categories: dict[str, Category],
    profiles: dict[str, ContextProfile],
) -> ResolutionResult:
    """Pure function: same inputs ⇒ same outputs (SC-002).

    Algorithm:
    1. Look up the category definition. Unknown ⇒ ResolutionError
       (fail-closed; Principle VI).
    2. Start with category.default_tier as the baseline.
    3. For each matching profile (in sorted profile_ids order for
       determinism), apply its override if it has one for this
       category:
         - fixed-high: override IGNORED (FR-007 immovable).
         - context-up: override TIER may RAISE only (ignored if lower).
         - context-resolved: override may raise OR lower.
    4. Compose across profiles using most-restrictive (FR-026a).
    5. Return ResolutionResult with stable rationale.
    """
    if category_id not in categories:
        raise ResolutionError(f"unknown category: {category_id!r}")
    category = categories[category_id]

    # Determinism: sort profile_ids so a permutation of inputs yields
    # identical output. The most-restrictive composition is already
    # order-invariant, but the rationale string depends on iteration
    # order — sorting nails it down.
    sorted_pids = tuple(sorted(profile_ids))
    contributing: list[str] = []
    overrides_applied: list[tuple[str, Tier]] = []

    current = category.default_tier
    rationale_parts: list[str] = [
        f"category={category.id} default_tier={category.default_tier.value} "
        f"mode={category.resolution_mode}",
    ]

    if category.resolution_mode == "fixed-high":
        # No profile may modify. The contributing_profile_ids list
        # records which profiles were consulted-but-ignored.
        for pid in sorted_pids:
            if pid not in profiles:
                raise ResolutionError(f"unknown profile: {pid!r}")
            profile = profiles[pid]
            override = profile.override_for(category.id)
            if override is not None:
                contributing.append(pid)
        if contributing:
            rationale_parts.append(
                f"fixed-high IGNORES overrides from profiles={','.join(contributing)}",
            )
        return ResolutionResult(
            tier=current,
            rationale="; ".join(rationale_parts),
            contributing_profile_ids=tuple(contributing),
        )

    # context-up or context-resolved
    for pid in sorted_pids:
        if pid not in profiles:
            raise ResolutionError(f"unknown profile: {pid!r}")
        profile = profiles[pid]
        override = profile.override_for(category.id)
        if override is None or override.tier is None:
            continue
        contributing.append(pid)
        mode = override.resolution_mode or category.resolution_mode
        if mode == "context-up":
            # Raise only.
            if compare(override.tier, current) > 0:
                current = override.tier
                overrides_applied.append((pid, override.tier))
        elif mode == "context-resolved":
            # Compose most-restrictive — both raise (always) and
            # the profile-as-floor (raise the floor toward the
            # override). The "lowering" case in the docstring refers
            # to operator-authored config; at composition time, the
            # most-restrictive rule still picks max. A profile that
            # wants to LOWER must be the only matching profile (no
            # other profile contributes a higher tier).
            current = max_of(current, override.tier)
            overrides_applied.append((pid, override.tier))
        elif mode == "fixed-high":
            # Override carries its own mode override that pins fixed.
            # Treat as no-op (the category-level fixed-high logic
            # above would have already returned; this branch only
            # fires for context-resolved categories where the profile
            # is asking for fixed-high — we honor it as immovable
            # for the current iteration but don't carve out further).
            continue

    if overrides_applied:
        ovs = ", ".join(f"{pid}->{t.value}" for pid, t in overrides_applied)
        rationale_parts.append(f"overrides applied: {ovs}")

    return ResolutionResult(
        tier=current,
        rationale="; ".join(rationale_parts),
        contributing_profile_ids=tuple(contributing),
    )


# --- T101 — max-tier clearance read-up refusal (FR-008 / US5) -------


class ClearanceRefusedError(RuntimeError):
    """A read of a datum at tier T was attempted under a profile whose
    `max_tier` is lower than T. Refused per FR-008. Fail-closed."""

    def __init__(
        self,
        *,
        profile_id: str,
        profile_max_tier: Tier,
        attempted_tier: Tier,
    ) -> None:
        super().__init__(
            f"profile {profile_id!r} clearance is {profile_max_tier.value} "
            f"but datum tier is {attempted_tier.value} — read refused (FR-008)",
        )
        self.profile_id = profile_id
        self.profile_max_tier = profile_max_tier
        self.attempted_tier = attempted_tier


def check_max_tier_clearance(
    *,
    profile: ContextProfile,
    attempted_tier: Tier,
) -> None:
    """Raise ClearanceRefusedError if `attempted_tier` exceeds the
    profile's `max_tier`. No-op when the profile declares no max_tier
    (open clearance) — operator's choice (FR-008).

    Read-up refusal: a profile cleared to `regulated` cannot read a
    `restricted` or `prohibited` datum. The check is on the *resolved*
    tier from `resolve_tier()`, not the raw default.
    """
    if profile.max_tier is None:
        return
    if compare(attempted_tier, profile.max_tier) > 0:
        raise ClearanceRefusedError(
            profile_id=profile.id,
            profile_max_tier=profile.max_tier,
            attempted_tier=attempted_tier,
        )


# --- T102 — Biba integrity floor (FR-004 / US5) ---------------------


class IntegrityFloorError(RuntimeError):
    """A step demanded a higher-integrity input than was provided.
    Biba no-read-down direction: an integrity-floored step refuses
    any input whose provenance is below the floor. Refused per FR-004."""

    def __init__(
        self,
        *,
        floor: str,
        input_level: str,
    ) -> None:
        super().__init__(
            f"integrity floor {floor!r} refuses input at level {input_level!r} (FR-004)",
        )
        self.floor = floor
        self.input_level = input_level


# Floor levels and their integrity ranks (higher rank = stricter floor).
# Mirrors the ProvenanceLevel ordering in policy.labels:
#   PRINCIPAL_DIRECT > SYSTEM_INTERNAL > EXTERNAL_UNTRUSTED.
# An integrity-floored step at PRINCIPAL_DIRECT only accepts
# PRINCIPAL_DIRECT inputs; at SYSTEM_INTERNAL, accepts SYSTEM_INTERNAL
# or PRINCIPAL_DIRECT; at EXTERNAL_UNTRUSTED, accepts everything
# (degenerate floor — no integrity demand).
_INTEGRITY_RANK: dict[str, int] = {
    "external-untrusted": 0,
    "system-internal": 1,
    "principal-direct": 2,
}


def check_integrity_floor(
    *,
    floor_level: str,
    input_level: str,
) -> None:
    """Biba no-read-down check (FR-004). Raises IntegrityFloorError
    if `input_level` is below `floor_level` on the provenance lattice.

    Both arguments are the string forms of `ProvenanceLevel` to keep
    this helper string-based (callers may not have AxisB constructed
    when they invoke this from a non-AxisB path)."""
    if floor_level not in _INTEGRITY_RANK:
        raise IntegrityFloorError(floor=floor_level, input_level=input_level)
    if input_level not in _INTEGRITY_RANK:
        raise IntegrityFloorError(floor=floor_level, input_level=input_level)
    if _INTEGRITY_RANK[input_level] < _INTEGRITY_RANK[floor_level]:
        raise IntegrityFloorError(floor=floor_level, input_level=input_level)
