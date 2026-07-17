"""Security-posture manifest (#304).

A **posture** is a NAMED BINDING over the EXISTING policy dials — it invents no
authority (see `docs/trust-model.md`; the daemon still owns all enforcement).
Selecting a posture chooses, as data:

  - `clearance_max_tier` — feeds the existing `clearance_max_tier` /
    `ContextProfile.max_tier` read-up ceiling. Not a floor; the presets hold it
    constant (they "differ by dial and pattern-default, never by floor").
  - `risk_preference` — the existing envelope dial {cautious, balanced, permissive}.
  - `flow_pattern_defaults` — the ONE genuinely new dial: the default execution
    mode (flow pattern) per sensitivity tier, consulted by `select_mode`.
  - `projection_only` — the #359 posture for untrusted-source raw readers
    (`inbox.read`): hidden from the planner (projection-only) or allowed raw.
  - `inspector_set` — which decision-inspector config entries are active.
  - `retention` — the daily-driver retention class.

Crucially, a posture may only ratchet **stricter** than the structural floors,
never below them (validated at load): the restricted-tier Pattern-3/5 floor
(FR-047) holds regardless of what a posture says. This is the config surface
that lets one operator run "strict / high-security-useful / low-friction" while
every posture inherits identical DENY floors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from capabledeputy.daily_driver import Retention
from capabledeputy.mode.dispatcher import ExecutionMode
from capabledeputy.policy.envelope import RiskPreference
from capabledeputy.policy.tiers import Tier


class PostureError(RuntimeError):
    """Fail-closed posture load/validation error (Principle VI)."""


# Planner-exposure strength of each flow pattern. Anti-injection strength rises:
# the less the planner can see, the less an injected instruction can exfiltrate
# or steer. TURN_LEVEL/PROGRAMMATIC expose raw values (exposure class 0);
# DUAL_LLM projects through a schema (1); REFERENCE/SEALED keep the planner
# data-blind (2). Used only to compare a posture default against the floor.
_PATTERN_STRENGTH: dict[ExecutionMode, int] = {
    ExecutionMode.TURN_LEVEL: 0,
    ExecutionMode.PROGRAMMATIC: 0,
    ExecutionMode.DUAL_LLM: 1,
    ExecutionMode.REFERENCE: 2,
    ExecutionMode.SEALED: 2,
}

# The structural floor per tier — the WEAKEST flow pattern the runtime will ever
# use for data at that tier, regardless of posture. RESTRICTED+ require Pattern
# 3/5 (FR-047). A posture default must be >= this (validated below).
_FLOOR_PATTERN: dict[Tier, ExecutionMode] = {
    Tier.NONE: ExecutionMode.TURN_LEVEL,
    Tier.SENSITIVE: ExecutionMode.TURN_LEVEL,
    Tier.REGULATED: ExecutionMode.TURN_LEVEL,
    Tier.RESTRICTED: ExecutionMode.REFERENCE,
    Tier.PROHIBITED: ExecutionMode.REFERENCE,
}

# Today's behavior AS DATA — the default posture when none is configured, so an
# unconfigured runtime behaves exactly as before this issue. `select_mode` still
# applies the restricted floor and the DUAL_LLM-needs-a-quarantined-extractor
# guard independently; these are the *defaults* it starts from per the session's
# highest tier.
DEFAULT_FLOW_PATTERN_DEFAULTS: dict[Tier, ExecutionMode] = {
    Tier.NONE: ExecutionMode.TURN_LEVEL,
    Tier.SENSITIVE: ExecutionMode.TURN_LEVEL,
    Tier.REGULATED: ExecutionMode.TURN_LEVEL,
    Tier.RESTRICTED: ExecutionMode.REFERENCE,
    Tier.PROHIBITED: ExecutionMode.SEALED,
}


@dataclass(frozen=True)
class Posture:
    """A named security posture: a value-binding over the existing dials."""

    id: str
    clearance_max_tier: Tier | None = None
    risk_preference: RiskPreference = RiskPreference.CAUTIOUS
    flow_pattern_defaults: dict[Tier, ExecutionMode] = field(
        default_factory=lambda: dict(DEFAULT_FLOW_PATTERN_DEFAULTS),
    )
    projection_only: bool = True  # #359 default: hide untrusted-source raw readers
    inspector_set: tuple[str, ...] = ()
    retention: Retention = Retention.METADATA

    def flow_pattern_for(self, tier: Tier) -> ExecutionMode:
        """The posture's default flow pattern for `tier` (falls back to the
        shipped default for a tier the posture didn't override)."""
        return self.flow_pattern_defaults.get(tier, DEFAULT_FLOW_PATTERN_DEFAULTS[tier])

    def validate(self) -> Posture:
        """Fail-closed: a posture may only ratchet STRICTER than the structural
        floor, never below it. Returns self so callers can chain."""
        for tier, mode in self.flow_pattern_defaults.items():
            if _PATTERN_STRENGTH[mode] < _PATTERN_STRENGTH[_FLOOR_PATTERN[tier]]:
                raise PostureError(
                    f"posture {self.id!r}: flow-pattern default {mode.value!r} for tier "
                    f"{tier.value!r} is weaker than the structural floor "
                    f"{_FLOOR_PATTERN[tier].value!r}; a posture may only ratchet "
                    "stricter, never below the floor (FR-047).",
                )
        return self


# The default posture (today's behavior) used when no posture is configured.
DEFAULT_POSTURE = Posture(id="__default__").validate()


# --- #305 — the three shipped presets ---------------------------------
#
# Three postures differing by DIAL and PATTERN-DEFAULT, never by floor: all
# three inherit identical structural DENY floors (credential exfil,
# untrusted→egress, irreversible-delete, the restricted Pattern-3/5 floor) —
# that invariant is what the #306 conformance harness fuzzes. All three keep
# `projection_only=True` (the #359 secure default): raw-allowed-with-taint is
# an explicit operator override via a custom posture, never a shipped preset.

BUILTIN_POSTURES: dict[str, Posture] = {
    # Pure security-model compliance: Pattern 3/5 for anything labeled,
    # tighteners only, metadata-only retention. Every egress gates.
    "strict": Posture(
        id="strict",
        risk_preference=RiskPreference.CAUTIOUS,
        flow_pattern_defaults={
            Tier.NONE: ExecutionMode.TURN_LEVEL,
            Tier.SENSITIVE: ExecutionMode.REFERENCE,
            Tier.REGULATED: ExecutionMode.REFERENCE,
            Tier.RESTRICTED: ExecutionMode.REFERENCE,
            Tier.PROHIBITED: ExecutionMode.SEALED,
        },
        projection_only=True,
        inspector_set=("after_hours_purchase_tightener",),
        retention=Retention.METADATA,
    ).validate(),
    # Pattern 3/5 for restricted, Pattern 2 (dual-LLM) for regulated, safe
    # relaxers active, redacted retention.
    "high-security-useful": Posture(
        id="high-security-useful",
        risk_preference=RiskPreference.BALANCED,
        flow_pattern_defaults={
            Tier.NONE: ExecutionMode.TURN_LEVEL,
            Tier.SENSITIVE: ExecutionMode.TURN_LEVEL,
            Tier.REGULATED: ExecutionMode.DUAL_LLM,
            Tier.RESTRICTED: ExecutionMode.REFERENCE,
            Tier.PROHIBITED: ExecutionMode.SEALED,
        },
        projection_only=True,
        inspector_set=("self_egress_relaxer", "after_hours_purchase_tightener"),
        retention=Retention.REDACTED,
    ).validate(),
    # Pattern 1 for regulated + 3/5 for restricted, broader relaxers, artifact
    # retention. NB: projection_only stays True even here — the low-friction
    # dial never silently reopens #359 turn-1 steering.
    "low-friction-practical": Posture(
        id="low-friction-practical",
        risk_preference=RiskPreference.PERMISSIVE,
        flow_pattern_defaults={
            Tier.NONE: ExecutionMode.TURN_LEVEL,
            Tier.SENSITIVE: ExecutionMode.TURN_LEVEL,
            Tier.REGULATED: ExecutionMode.TURN_LEVEL,
            Tier.RESTRICTED: ExecutionMode.REFERENCE,
            Tier.PROHIBITED: ExecutionMode.SEALED,
        },
        projection_only=True,
        inspector_set=("self_egress_relaxer", "after_hours_purchase_tightener"),
        retention=Retention.ARTIFACT,
    ).validate(),
}


def resolve_posture(
    posture_id: str,
    custom: dict[str, Posture] | None = None,
) -> Posture:
    """Resolve a posture id against the shipped presets plus any operator
    postures. Fail-closed (Principle VI): an unknown id refuses rather than
    silently falling back to a default, and a custom posture may not shadow a
    builtin preset id (so `strict` always means the shipped strict)."""
    custom = custom or {}
    shadowed = set(custom) & set(BUILTIN_POSTURES)
    if shadowed:
        raise PostureError(
            f"custom posture(s) {sorted(shadowed)} shadow builtin preset ids; "
            "rename them — builtin preset semantics must be stable.",
        )
    available = {**BUILTIN_POSTURES, **custom}
    posture = available.get(posture_id)
    if posture is None:
        raise PostureError(
            f"unknown posture {posture_id!r}; known postures: {sorted(available)}",
        )
    return posture


def _parse_flow_pattern_defaults(raw: object, *, posture_id: str) -> dict[Tier, ExecutionMode]:
    if raw is None:
        return dict(DEFAULT_FLOW_PATTERN_DEFAULTS)
    if not isinstance(raw, dict):
        raise PostureError(f"posture {posture_id!r}: flow_pattern_defaults must be a mapping")
    out = dict(DEFAULT_FLOW_PATTERN_DEFAULTS)
    for tier_raw, mode_raw in raw.items():
        try:
            tier = Tier(str(tier_raw))
        except ValueError as e:
            raise PostureError(f"posture {posture_id!r}: bad tier {tier_raw!r}") from e
        try:
            mode = ExecutionMode(str(mode_raw))
        except ValueError as e:
            raise PostureError(
                f"posture {posture_id!r}: bad flow pattern {mode_raw!r} for tier {tier.value!r}",
            ) from e
        out[tier] = mode
    return out


def _parse_posture(index: int, raw: object) -> Posture:
    if not isinstance(raw, dict):
        raise PostureError(f"postures[{index}] is not an object")
    try:
        pid = str(raw["id"])
    except KeyError:
        raise PostureError(f"postures[{index}] missing required: 'id'") from None
    try:
        clearance = Tier(str(raw["clearance_max_tier"])) if raw.get("clearance_max_tier") else None
        risk = RiskPreference(str(raw.get("risk_preference", "cautious")))
        retention = Retention(str(raw.get("retention", "metadata")))
    except ValueError as e:
        raise PostureError(f"postures[{index}] ({pid!r}): {e}") from e
    inspector_set = tuple(str(s) for s in (raw.get("inspector_set") or []))
    projection_only = bool(raw.get("projection_only", True))
    flow_defaults = _parse_flow_pattern_defaults(raw.get("flow_pattern_defaults"), posture_id=pid)
    return Posture(
        id=pid,
        clearance_max_tier=clearance,
        risk_preference=risk,
        flow_pattern_defaults=flow_defaults,
        projection_only=projection_only,
        inspector_set=inspector_set,
        retention=retention,
    ).validate()


def load_postures(path: Path) -> dict[str, Posture]:
    """Load named postures from `postures.yaml`. Fail-closed on missing/
    unparseable file or any invalid posture (same contract as load_profiles).
    Missing-or-empty `postures:` yields an empty dict."""
    if not path.is_file():
        raise PostureError(f"postures config missing: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise PostureError(f"postures config unparseable: {path} — {e}") from e
    if data is None:
        return {}
    raw_list = data.get("postures") or []
    if not isinstance(raw_list, list):
        raise PostureError(f"postures config: 'postures' must be a list: {path}")
    out: dict[str, Posture] = {}
    for i, raw in enumerate(raw_list):
        posture = _parse_posture(i, raw)
        if posture.id in out:
            raise PostureError(f"postures[{i}]: duplicate id {posture.id!r}")
        out[posture.id] = posture
    return out
