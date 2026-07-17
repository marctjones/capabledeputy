"""Operator requirement DSL (#307).

A small declarative assertion format an operator writes alongside a posture,
so a deployment can state its OWN hard guarantees and have the daemon refuse to
start (or CI fail) if the selected posture + inspector configuration does not
satisfy them. Two classes of requirement:

  - **Built-in hard requirements** (`BUILTIN_REQUIREMENTS`): the project's
    genuinely UNCONDITIONAL floors — untrusted-never-egress (explicitly never
    crossable), the untrusted-source EXPOSURE floor (raw readers hidden in
    exposure-limited modes, #302), and the restricted mode floor. Always checked,
    cannot be disabled. Guarantees that are operator-OVERRIDABLE by design are
    deliberately NOT builtin (asserting them absolute would be a false guarantee):
    health/financial DENY-egress (the `personal`+ratified-`crosses_floor` path can
    suppress them) and planner-blindness in TURN_LEVEL (the `projection_only`
    knob). Those ship as operator custom requirements instead.
  - **Operator custom requirements**: additive, per-deployment, loaded from a
    `requirements.yaml`. Checked exactly like the built-ins, against the
    deployment's real trust profile + rules (so a health-deny requirement is
    correctly reported UNMET if a ratified personal-crossing rule breaches it).

The checker VERIFIES a requirement by exercising the real decision surfaces — it
does not re-implement policy. Crucially (and this is why the DSL earns its
keep) a requirement is checked against the *effective* decision, i.e. AFTER the
posture's decision-inspectors run, replaying the chokepoint's floor guard
(`tools/policy_hooks.py`) as a pure function. So a `SelfEgressRelaxer` an
operator enabled that would relax a REQUIRE_APPROVAL egress to ALLOW is caught
here if it conflicts with a declared requirement.

Requirement semantics are HONEST about the engine's channel-asymmetry: credential
/ confidential-category containment on the email channel is REQUIRE_APPROVAL, not
DENY (the DENY floor is on the web.fetch channel + the surface-B mode floor + the
surface-C tool-hiding). So the checkable guarantee for a confidential category
is `never_silent_egress` (never a bare ALLOW), plus the hard DENY floors where
the engine actually denies. A requirement asserting a blanket DENY the engine
does not make would be a false guarantee, so the DSL does not offer one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from capabledeputy.mode.dispatcher import (
    UNTRUSTED_SOURCE_RAW_READERS,
    ExecutionMode,
    ModeSelectionError,
    filter_tools_for_mode,
    select_mode,
)
from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.decision_rules import DecisionRules
from capabledeputy.policy.engine import PolicyDecision, decide
from capabledeputy.policy.labels import (
    AxisD,
    CategoryTag,
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
)
from capabledeputy.policy.posture import Posture
from capabledeputy.policy.reversibility import (
    ReversalAgent,
    ReversibilityDegree,
    ReversibilityLabel,
)
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier
from capabledeputy.substrate.decision_inspector_port import (
    compose_inspector_outcomes,
    is_strictly_less_restrictive,
)

# Fixed decision clock — requirement checks must be deterministic and never
# depend on wall-clock (Principle I). Noon avoids the after-hours tightener
# window so a check reflects the base policy, not a time-of-day artifact.
_NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)


class RequirementError(RuntimeError):
    """A requirements file is malformed / unparseable. Fail-closed
    (Principle VI): a requirement that cannot be understood is refused, never
    silently skipped."""


class RequirementViolationError(RuntimeError):
    """One or more requirements were NOT satisfied by the active posture +
    inspector configuration. Raised to fail daemon start / CI."""

    def __init__(self, results: list[RequirementResult]) -> None:
        self.results = results
        unmet = [r for r in results if not r.satisfied]
        lines = "\n".join(f"  - {r.requirement.id}: {r.detail}" for r in unmet)
        super().__init__(f"{len(unmet)} operator requirement(s) unmet:\n{lines}")


class RequirementKind(StrEnum):
    """The verifiable requirement predicates. Each maps to a concrete probe over
    the decision surfaces — no free-form logic, so every requirement is
    deterministically checkable."""

    # Confidential category never egresses on a bare ALLOW (any channel), i.e.
    # a human or a floor always stands between labeled data and egress.
    NEVER_SILENT_EGRESS = "never_silent_egress"  # param: category
    # A category is hard-DENIED on the named egress channels.
    DENY_EGRESS = "deny_egress"  # params: category, channels
    # External-untrusted provenance can never egress on any channel (DENY).
    UNTRUSTED_NEVER_EGRESS = "untrusted_never_egress"
    # Untrusted-SOURCE raw readers are hidden in the exposure-limited modes
    # (the #302 knob-independent floor). UNCONDITIONAL — no posture can override.
    UNTRUSTED_SOURCE_EXPOSURE_FLOOR = "untrusted_source_exposure_floor"
    # STRONGER, OPT-IN: the planner is blind to untrusted-source raw readers in
    # EVERY mode including TURN_LEVEL (i.e. projection_only). This closes the
    # CaMeL turn-1 steering window; a deployment that runs `projection_only=false`
    # (raw-allowed-with-taint) deliberately declines it, so it is an operator
    # requirement, not a non-negotiable builtin.
    PLANNER_BLIND_TO_UNTRUSTED_SOURCE = "planner_blind_to_untrusted_source"
    # Restricted-tier data never runs under a planner-exposing mode.
    RESTRICTED_MODE_FLOOR = "restricted_mode_floor"


# Egress channels probed for the confidentiality requirements, with the
# effect_class spelling the engine matches.
_EGRESS_CHANNELS: tuple[CapabilityKind, ...] = (
    CapabilityKind.SEND_EMAIL,
    CapabilityKind.SEND_MESSAGE,
    CapabilityKind.QUEUE_PURCHASE,
)
_CHANNEL_NAMES: dict[str, CapabilityKind] = {
    "send_email": CapabilityKind.SEND_EMAIL,
    "send_message": CapabilityKind.SEND_MESSAGE,
    "queue_purchase": CapabilityKind.QUEUE_PURCHASE,
    "web_fetch": CapabilityKind.WEB_FETCH,
}
_CATEGORY_TIER: dict[str, Tier] = {
    "health": Tier.RESTRICTED,
    "financial": Tier.RESTRICTED,
    "credentials": Tier.RESTRICTED,
    "personal": Tier.REGULATED,
    "proprietary_work": Tier.REGULATED,
}

# The effect_class spelling + effective reversibility the runtime attaches per
# egress channel — REQUIRED so the probe sees the same reversibility gate the
# real chokepoint applies (social.* egress is hard-coded irreversible, which is
# what turns an otherwise-ALLOW confidential email into REQUIRE_APPROVAL). Omit
# them and the probe under-constrains and reads a false ALLOW.
_IRREVERSIBLE = ReversibilityLabel(ReversibilityDegree.IRREVERSIBLE, ReversalAgent.EXTERNAL)
_CHANNEL_EFFECT: dict[CapabilityKind, tuple[str | None, ReversibilityLabel | None]] = {
    CapabilityKind.SEND_EMAIL: ("social.send_email", _IRREVERSIBLE),
    CapabilityKind.SEND_MESSAGE: ("social.send_message", _IRREVERSIBLE),
    CapabilityKind.QUEUE_PURCHASE: ("commerce.purchase", _IRREVERSIBLE),
    CapabilityKind.WEB_FETCH: (None, None),  # gated by the destination-aware fetch floor
}


@dataclass(frozen=True)
class Requirement:
    """A single operator-declared (or built-in) requirement."""

    id: str
    kind: RequirementKind
    description: str
    builtin: bool = False
    category: str | None = None
    channels: tuple[CapabilityKind, ...] = ()


@dataclass(frozen=True)
class RequirementResult:
    requirement: Requirement
    satisfied: bool
    detail: str


@dataclass(frozen=True)
class DeploymentContext:
    """The runtime configuration a requirement is checked against — the active
    posture plus the decision surface's inputs that affect a decision's
    outcome. `trust_profile_is_personal` + `rules_v2` are load-bearing: they let
    the probe see the `personal`-profile human-ratified `crosses_floor` path that
    can legitimately suppress a health/financial floor, so the checker never
    falsely reports a conditional guarantee as met."""

    posture: Posture
    decision_inspectors: tuple[Any, ...] = ()
    clearance_max_tier: Tier | None = None
    trust_profile_is_personal: bool = False
    rules_v2: DecisionRules | None = None


# --- probe machinery ------------------------------------------------------


def _broad_cap(kind: CapabilityKind) -> frozenset[Capability]:
    return frozenset(
        {
            Capability(
                kind=kind,
                pattern="*",
                origin=CapabilityOrigin.USER_APPROVED,
                allows_destructive=True,
            ),
        },
    )


def _labels_for(category: str) -> LabelState:
    tier = _CATEGORY_TIER.get(category, Tier.RESTRICTED)
    return LabelState(a=frozenset({CategoryTag(category, tier)}))


def _self_addresses(decision_inspectors: tuple[Any, ...]) -> tuple[str, ...]:
    """Pull any `self_addresses` an inspector (e.g. SelfEgressRelaxer) is
    configured with, so the egress probe adversarially targets exactly the
    addresses that inspector would relax."""
    out: list[str] = []
    for insp in decision_inspectors:
        for addr in getattr(insp, "self_addresses", ()):  # frozenset or ()
            out.append(str(addr))
    return tuple(out)


def _apply_inspectors(
    base: PolicyDecision,
    action: Action,
    decision_inspectors: tuple[Any, ...],
) -> Decision:
    """Compose the posture's decision-inspectors over `base` and return the
    EFFECTIVE decision, replaying the chokepoint's floor guard
    (`tools/policy_hooks.py` apply_decision_inspectors) as a pure function:
    a relax is applied only against a REQUIRE_APPROVAL base; a WARN only against
    an ALLOW base; a DENY/OVERRIDE floor is never loosened."""
    if not decision_inspectors:
        return base.decision
    inspect_action = SimpleNamespace(
        kind=action.kind,
        target=action.target,
        amount=action.amount,
        tool_name="requirement-probe",
        effect_class=None,
        relationship_group_ids=(),
        effective_reversibility=None,
    )
    session = SimpleNamespace(id=None, label_state=LabelState())
    outcomes: list[tuple[str, Any]] = []
    for insp in decision_inspectors:
        oc = insp.inspect(action=inspect_action, session=session, proposed_outcome=base)
        if oc is not None:
            outcomes.append((getattr(insp, "name", "<unknown>"), oc))
    composed = compose_inspector_outcomes(base.decision, outcomes)
    if composed is None:
        return base.decision
    new = composed[0]
    if (
        is_strictly_less_restrictive(new, base.decision)
        and base.decision != Decision.REQUIRE_APPROVAL
    ):
        return base.decision  # relax refused against a floor
    if new == Decision.WARN and base.decision != Decision.ALLOW:
        return base.decision  # WARN only weakens an ALLOW
    return new


def _egress_base_decision(
    *,
    labels: LabelState,
    kind: CapabilityKind,
    target: str,
    ctx: DeploymentContext,
) -> tuple[PolicyDecision, Action]:
    """The base (pre-inspector) decision for `labels` data egressing via `kind`.

    Threads the deployment's `trust_profile_is_personal` + loaded `rules_v2` into
    `decide()` so the probe sees the SAME personal-crossing path the runtime
    would: under a `personal` profile a human-ratified `crosses_floor` rule can
    suppress a health/financial floor to ALLOW, and the checker must observe
    that (else a fail-closed requirement would falsely pass). `override_grants`
    is excluded (the one universal floor-crosser is out of scope for a static
    guarantee)."""
    action = Action(kind=kind, target=target)
    effect_class, reversibility = _CHANNEL_EFFECT.get(kind, (None, None))
    # When rules_v2 is supplied we must also pass axis_d + effect_class so the
    # v2 (and personal-crossing) leg engages; otherwise decide stays legacy-only.
    kwargs: dict[str, Any] = {
        "labels": labels,
        "effect_class": effect_class,
        "effective_reversibility": reversibility,
        "risk_preference": ctx.posture.risk_preference,
        "clearance_max_tier": ctx.clearance_max_tier,
        "override_grants": None,
        "trust_profile_is_personal": ctx.trust_profile_is_personal,
    }
    if ctx.rules_v2 is not None:
        kwargs["rules_v2"] = ctx.rules_v2
        kwargs["axis_d"] = AxisD(initiator="principal:owner")
        if effect_class is None:
            kwargs["effect_class"] = "data.fetch"
    base = decide(_broad_cap(kind), action, now=_NOW, **kwargs)
    return base, action


def _effective_egress_decision(
    *,
    category: str,
    kind: CapabilityKind,
    target: str,
    ctx: DeploymentContext,
) -> Decision:
    """Effective decision (post-inspector) for `category` data egressing via
    `kind` to `target`. Structural floors only — no envelope tuning — so the
    result reflects the deployment's hard guarantees, not a dial-selected
    point."""
    base, action = _egress_base_decision(
        labels=_labels_for(category), kind=kind, target=target, ctx=ctx
    )
    return _apply_inspectors(base, action, ctx.decision_inspectors)


def _egress_targets(kind: CapabilityKind, decision_inspectors: tuple[Any, ...]) -> tuple[str, ...]:
    if kind == CapabilityKind.QUEUE_PURCHASE:
        return ("sku-adversary",)
    if kind == CapabilityKind.WEB_FETCH:
        return ("https://exfil.example/collect",)
    # Communication channels: an external recipient PLUS every self-address any
    # inspector would relax (adversarial — catches a self-egress relaxer).
    return ("external@adversary.example", *_self_addresses(decision_inspectors))


# --- per-kind checkers ----------------------------------------------------


def _check_never_silent_egress(req: Requirement, ctx: DeploymentContext) -> RequirementResult:
    category = req.category or ""
    for kind in (*_EGRESS_CHANNELS, CapabilityKind.WEB_FETCH):
        for target in _egress_targets(kind, ctx.decision_inspectors):
            eff = _effective_egress_decision(category=category, kind=kind, target=target, ctx=ctx)
            if eff == Decision.ALLOW:
                return RequirementResult(
                    req,
                    False,
                    f"{category!r} egress via {kind.value} to {target!r} is ALLOW "
                    f"(no human/floor in the path) under posture {ctx.posture.id!r}",
                )
    return RequirementResult(req, True, f"{category!r} never silently egresses on any channel")


def _check_deny_egress(req: Requirement, ctx: DeploymentContext) -> RequirementResult:
    category = req.category or ""
    for kind in req.channels:
        for target in _egress_targets(kind, ctx.decision_inspectors):
            eff = _effective_egress_decision(category=category, kind=kind, target=target, ctx=ctx)
            if eff != Decision.DENY:
                return RequirementResult(
                    req,
                    False,
                    f"{category!r} egress via {kind.value} to {target!r} is {eff.value}, "
                    f"expected DENY, under posture {ctx.posture.id!r}",
                )
    return RequirementResult(
        req,
        True,
        f"{category!r} is DENIED on {[k.value for k in req.channels]}",
    )


def _check_untrusted_never_egress(req: Requirement, ctx: DeploymentContext) -> RequirementResult:
    labels = LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)}))
    for kind in _EGRESS_CHANNELS:
        for target in _egress_targets(kind, ctx.decision_inspectors):
            base, action = _egress_base_decision(labels=labels, kind=kind, target=target, ctx=ctx)
            eff = _apply_inspectors(base, action, ctx.decision_inspectors)
            if eff != Decision.DENY:
                return RequirementResult(
                    req,
                    False,
                    f"external-untrusted egress via {kind.value} to {target!r} is "
                    f"{eff.value}, expected DENY",
                )
    return RequirementResult(req, True, "external-untrusted provenance never egresses")


def _untrusted_source_probe_tools() -> list[Any]:
    from capabledeputy.tools.registry import ToolDefinition

    async def _noop(args: dict[str, Any], context: Any) -> Any:  # pragma: no cover
        raise NotImplementedError

    return [
        ToolDefinition(
            name=n, description="probe", capability_kind=CapabilityKind.READ_FS, handler=_noop
        )
        for n in (*sorted(UNTRUSTED_SOURCE_RAW_READERS), "inbox.list")
    ]


def _check_untrusted_source_exposure_floor(
    req: Requirement,
    ctx: DeploymentContext,
) -> RequirementResult:
    """UNCONDITIONAL builtin: untrusted-source raw readers are hidden in the
    exposure-limited modes (DUAL_LLM/REFERENCE/SEALED) — the #302 knob-independent
    floor. No posture (not even projection_only=False) can re-expose them here, so
    this is a true non-negotiable tripwire."""
    tools = _untrusted_source_probe_tools()
    for mode in (ExecutionMode.DUAL_LLM, ExecutionMode.REFERENCE, ExecutionMode.SEALED):
        visible = {t.name for t in filter_tools_for_mode(tools, mode, ctx.posture)}
        leaked = set(UNTRUSTED_SOURCE_RAW_READERS) & visible
        if leaked:  # pragma: no cover — the #302 floor makes this unreachable
            return RequirementResult(
                req,
                False,
                f"untrusted-source raw readers {sorted(leaked)} visible in {mode.value}",
            )
    return RequirementResult(
        req, True, "untrusted-source raw readers are hidden in exposure-limited modes"
    )


def _check_planner_blind_to_untrusted_source(
    req: Requirement,
    ctx: DeploymentContext,
) -> RequirementResult:
    """OPT-IN operator requirement: the planner is blind to untrusted-source raw
    readers in EVERY mode, including TURN_LEVEL (i.e. projection_only). Closes the
    CaMeL turn-1 steering window. A posture with projection_only=False
    (raw-allowed-with-taint) deliberately declines this and fails the check — that
    is the operator's designed trade-off, so it is a requirement, not a builtin."""
    posture = ctx.posture
    tools = _untrusted_source_probe_tools()
    turn_visible = {t.name for t in filter_tools_for_mode(tools, ExecutionMode.TURN_LEVEL, posture)}
    leaked = set(UNTRUSTED_SOURCE_RAW_READERS) & turn_visible
    if leaked:
        return RequirementResult(
            req,
            False,
            f"posture {posture.id!r} (projection_only={posture.projection_only}) exposes "
            f"untrusted-source raw readers {sorted(leaked)} to the planner in TURN_LEVEL "
            "(raw-allowed-with-taint) — declining the planner-blind requirement",
        )
    return RequirementResult(req, True, "planner is blind to untrusted-source raw readers")


def _check_restricted_mode_floor(req: Requirement, ctx: DeploymentContext) -> RequirementResult:
    from capabledeputy.tools.registry import ToolRegistry

    posture = ctx.posture
    restricted = LabelState(a=frozenset({CategoryTag("health", Tier.RESTRICTED)}))
    # A degenerate surface (no handle / sandbox) must fail closed, never select a
    # planner-exposing mode.
    try:
        mode, _ = select_mode(restricted, ToolRegistry(), posture=posture)
    except ModeSelectionError:
        return RequirementResult(
            req, True, "restricted data fails closed without a protective sink"
        )
    if mode in {ExecutionMode.TURN_LEVEL, ExecutionMode.DUAL_LLM, ExecutionMode.PROGRAMMATIC}:
        return RequirementResult(
            req,
            False,
            f"restricted data selected planner-exposing mode {mode.value} under {posture.id!r}",
        )
    return RequirementResult(req, True, f"restricted data routes to {mode.value}")


_CHECKERS = {
    RequirementKind.NEVER_SILENT_EGRESS: _check_never_silent_egress,
    RequirementKind.DENY_EGRESS: _check_deny_egress,
    RequirementKind.UNTRUSTED_NEVER_EGRESS: _check_untrusted_never_egress,
    RequirementKind.UNTRUSTED_SOURCE_EXPOSURE_FLOOR: _check_untrusted_source_exposure_floor,
    RequirementKind.PLANNER_BLIND_TO_UNTRUSTED_SOURCE: _check_planner_blind_to_untrusted_source,
    RequirementKind.RESTRICTED_MODE_FLOOR: _check_restricted_mode_floor,
}


def check_requirement(
    req: Requirement,
    *,
    posture: Posture,
    decision_inspectors: tuple[Any, ...] = (),
    clearance_max_tier: Tier | None = None,
    trust_profile_is_personal: bool = False,
    rules_v2: DecisionRules | None = None,
) -> RequirementResult:
    """Verify a single requirement against the active posture + inspectors and
    the deployment's trust profile + rules."""
    ctx = DeploymentContext(
        posture=posture,
        decision_inspectors=decision_inspectors,
        clearance_max_tier=clearance_max_tier,
        trust_profile_is_personal=trust_profile_is_personal,
        rules_v2=rules_v2,
    )
    return _CHECKERS[req.kind](req, ctx)


# --- built-in hard requirements (always checked, non-disableable) ---------

BUILTIN_REQUIREMENTS: tuple[Requirement, ...] = (
    Requirement(
        id="builtin.untrusted-never-egress",
        kind=RequirementKind.UNTRUSTED_NEVER_EGRESS,
        description="External-untrusted provenance can never egress on any channel.",
        builtin=True,
    ),
    Requirement(
        id="builtin.untrusted-source-exposure-floor",
        kind=RequirementKind.UNTRUSTED_SOURCE_EXPOSURE_FLOOR,
        description="Untrusted-source raw readers are hidden in exposure-limited modes (#302).",
        builtin=True,
    ),
    Requirement(
        id="builtin.restricted-mode-floor",
        kind=RequirementKind.RESTRICTED_MODE_FLOOR,
        description="Restricted-tier data never runs under a planner-exposing mode (FR-047).",
        builtin=True,
    ),
)
# NB: only the three floors above are truly UNCONDITIONAL, so only they are
# non-negotiable builtins. Two guarantees that LOOK structural are actually
# operator-overridable by design, so they are offered as CUSTOM requirements
# (see `configs/requirements.example.yaml`) rather than asserted as absolute:
#   * health / financial DENY-egress — the engine's `personal`-profile +
#     human-ratified `crosses_floor` path (engine.py `_compose_with_conflict_
#     invariant`) can legitimately suppress them over the operator's OWN data.
#   * planner-blindness to untrusted-source raw readers in TURN_LEVEL (the
#     `projection_only` knob) — a posture may run `projection_only=False`
#     (raw-allowed-with-taint), accepting the CaMeL turn-1 window while the
#     unconditional exposure floor above + the untrusted-never-egress floor
#     still hold. Deployments wanting the hard stance declare
#     `planner_blind_to_untrusted_source`.
# Asserting either as a non-negotiable builtin would forbid a designed override
# and overstate the guarantee — the exact thing this DSL refuses.


# --- YAML loader (operator custom requirements) ---------------------------


def _parse_requirement(index: int, raw: object) -> Requirement:
    if not isinstance(raw, dict):
        raise RequirementError(f"requirements[{index}] is not an object")
    try:
        rid = str(raw["id"])
    except KeyError:
        raise RequirementError(f"requirements[{index}] missing required: 'id'") from None
    try:
        kind = RequirementKind(str(raw["kind"]))
    except KeyError:
        raise RequirementError(
            f"requirements[{index}] ({rid!r}) missing required: 'kind'"
        ) from None
    except ValueError as e:
        raise RequirementError(f"requirements[{index}] ({rid!r}): {e}") from e
    description = str(raw.get("description", ""))
    category = raw.get("category")
    category = str(category) if category is not None else None

    channels: tuple[CapabilityKind, ...] = ()
    if raw.get("channels") is not None:
        chans = raw["channels"]
        if not isinstance(chans, list):
            raise RequirementError(f"requirements[{index}] ({rid!r}): 'channels' must be a list")
        parsed: list[CapabilityKind] = []
        for c in chans:
            key = str(c).lower()
            if key not in _CHANNEL_NAMES:
                raise RequirementError(
                    f"requirements[{index}] ({rid!r}): unknown channel {c!r}; "
                    f"known: {sorted(_CHANNEL_NAMES)}",
                )
            parsed.append(_CHANNEL_NAMES[key])
        channels = tuple(parsed)

    # Fail-closed cross-field validation: category-scoped kinds need a category.
    if kind in (RequirementKind.NEVER_SILENT_EGRESS, RequirementKind.DENY_EGRESS) and not category:
        raise RequirementError(
            f"requirements[{index}] ({rid!r}): kind {kind.value!r} requires a 'category'",
        )
    if kind == RequirementKind.DENY_EGRESS and not channels:
        raise RequirementError(
            f"requirements[{index}] ({rid!r}): kind 'deny_egress' requires 'channels'",
        )
    return Requirement(
        id=rid,
        kind=kind,
        description=description,
        builtin=False,
        category=category,
        channels=channels,
    )


def load_requirements(path: Path) -> tuple[Requirement, ...]:
    """Load operator custom requirements from `requirements.yaml`. Fail-closed
    on a missing/unparseable file or any invalid requirement. A missing-or-empty
    `requirements:` yields an empty tuple (the built-ins are always checked
    regardless)."""
    if not path.is_file():
        raise RequirementError(f"requirements config missing: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise RequirementError(f"requirements config unparseable: {path} — {e}") from e
    if data is None:
        return ()
    raw_list = data.get("requirements") or []
    if not isinstance(raw_list, list):
        raise RequirementError(f"requirements config: 'requirements' must be a list: {path}")
    out: list[Requirement] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_list):
        req = _parse_requirement(i, raw)
        if req.id in seen or req.id.startswith("builtin."):
            raise RequirementError(
                f"requirements[{i}]: id {req.id!r} duplicates a prior/built-in id",
            )
        seen.add(req.id)
        out.append(req)
    return tuple(out)


# --- verification entry points --------------------------------------------


def verify_requirements(
    *,
    posture: Posture,
    decision_inspectors: tuple[Any, ...] = (),
    clearance_max_tier: Tier | None = None,
    custom: tuple[Requirement, ...] = (),
    trust_profile_is_personal: bool = False,
    rules_v2: DecisionRules | None = None,
) -> list[RequirementResult]:
    """Check the built-in hard requirements PLUS any operator custom ones against
    the active posture + inspectors, verified against the deployment's trust
    profile + rules. Returns one result per requirement (built-ins first). Does
    not raise — callers decide (use `enforce_requirements` to fail closed)."""
    results: list[RequirementResult] = []
    for req in (*BUILTIN_REQUIREMENTS, *custom):
        results.append(
            check_requirement(
                req,
                posture=posture,
                decision_inspectors=decision_inspectors,
                clearance_max_tier=clearance_max_tier,
                trust_profile_is_personal=trust_profile_is_personal,
                rules_v2=rules_v2,
            ),
        )
    return results


def enforce_requirements(
    *,
    posture: Posture,
    decision_inspectors: tuple[Any, ...] = (),
    clearance_max_tier: Tier | None = None,
    custom: tuple[Requirement, ...] = (),
    trust_profile_is_personal: bool = False,
    rules_v2: DecisionRules | None = None,
) -> list[str]:
    """Verify requirements and RAISE `RequirementViolationError` if any is unmet —
    the daemon-start / CI gate. Returns human-readable log lines on success."""
    results = verify_requirements(
        posture=posture,
        decision_inspectors=decision_inspectors,
        clearance_max_tier=clearance_max_tier,
        custom=custom,
        trust_profile_is_personal=trust_profile_is_personal,
        rules_v2=rules_v2,
    )
    unmet = [r for r in results if not r.satisfied]
    if unmet:
        raise RequirementViolationError(results)
    return [
        f"[requirements] {len(results)} requirement(s) satisfied "
        f"({sum(1 for r in results if r.requirement.builtin)} built-in, "
        f"{len(custom)} operator) under posture {posture.id!r}",
    ]
