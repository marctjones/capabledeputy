"""003 US2 — DecisionRule evaluation with never-auto default.

A DecisionRule is a human-authored predicate over axes A/B/D + effect
class + target that, if matched, says "this exact context is expected
enough to auto-approve" (FR-010, FR-014). Without ANY matching rule,
every consequential action defaults to `suggest` or `deny` — never
`auto` (FR-011 / SC-003). The never-auto default is what makes the
asymmetry invariant (FR-031) hold: the model can suggest a rule but
cannot author one that fires; only a human-ratified rule in
configs/rules.yaml can produce `auto`.

This module is the pure-function evaluator. Wire-in to the existing
engine.decide() chokepoint lands with a wider refactor (T044) — for
now the evaluator is independently testable + invocable.

`bounded-relax` (FR-026 b-c) is partially implemented here: a rule
may relax (move toward auto) only within the cell's envelope; full
envelope composition lands in US6 (T076-T077). For US2 the simpler
invariant is enforced: a non-deterministic relax input is REFUSED.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from capabledeputy.policy.labels import AxisA, AxisB, AxisD


class RuleOutcome(StrEnum):
    """Decision outcomes ratchet-ordered by autonomy:
    DENY < REQUIRE_APPROVAL < SUGGEST < AUTO.
    Per FR-011, the never-auto default is SUGGEST (or DENY if the
    operator declares the cell as such); only a matched human-ratified
    rule may yield AUTO.
    """

    DENY = "deny"
    REQUIRE_APPROVAL = "require-approval"
    SUGGEST = "suggest"
    AUTO = "auto"


_OUTCOME_RANK: dict[RuleOutcome, int] = {
    RuleOutcome.DENY: 0,
    RuleOutcome.REQUIRE_APPROVAL: 1,
    RuleOutcome.SUGGEST: 2,
    RuleOutcome.AUTO: 3,
}


def _most_restrictive(*outcomes: RuleOutcome) -> RuleOutcome:
    """Most-restrictive composition (FR-026a baseline). Fail-closed
    on empty input — never silently degrade."""
    if not outcomes:
        raise ValueError("_most_restrictive() requires at least one outcome")
    return min(outcomes, key=lambda o: _OUTCOME_RANK[o])


class DecisionRuleError(RuntimeError):
    """Malformed rules.yaml; fail-closed per Principle VI."""


@dataclass(frozen=True)
class RulePredicate:
    """A rule's `when:` clause. Each optional field, if set, requires
    the corresponding axis attribute to match exactly. Missing fields
    match anything (wildcard)."""

    axis_a_category: str | None = None
    axis_b_provenance: str | None = None
    effect_class: str | None = None  # axis C lives on tool/cap, named here for symmetry
    axis_d_initiator: str | None = None
    axis_d_counterparty: str | None = None
    axis_d_relationship_group_id: str | None = None
    axis_d_expectedness: str | None = None  # "expected" | "anomalous"
    axis_d_reversibility_degree: str | None = None
    target: str | None = None  # exact string match if set

    def matches(
        self,
        *,
        axis_a: AxisA,
        axis_b: AxisB,
        axis_d: AxisD,
        effect_class: str,
        target: str,
    ) -> bool:
        if self.target is not None and self.target != target:
            return False
        if self.effect_class is not None and self.effect_class != effect_class:
            return False
        if self.axis_a_category is not None and not any(
            c.category == self.axis_a_category for c in axis_a.categories
        ):
            return False
        if self.axis_b_provenance is not None and not any(
            e.level.value == self.axis_b_provenance for e in axis_b.entries
        ):
            return False
        if self.axis_d_initiator is not None and axis_d.initiator != self.axis_d_initiator:
            return False
        if self.axis_d_counterparty is not None and axis_d.counterparty != self.axis_d_counterparty:
            return False
        if (
            self.axis_d_relationship_group_id is not None
            and self.axis_d_relationship_group_id not in axis_d.relationship_group_ids
        ):
            return False
        if self.axis_d_expectedness is not None and axis_d.expectedness != self.axis_d_expectedness:
            return False
        return not (
            self.axis_d_reversibility_degree is not None
            and axis_d.reversibility.get("degree") != self.axis_d_reversibility_degree
        )


@dataclass(frozen=True)
class DecisionRule:
    rule_id: str
    predicate: RulePredicate
    outcome: RuleOutcome
    rationale: str
    risk_ids: tuple[str, ...] = field(default_factory=tuple)
    human_ratified_by: str | None = None  # FR-014 — only ratified rules fire


@dataclass(frozen=True)
class DecisionRules:
    rules: tuple[DecisionRule, ...]


@dataclass(frozen=True)
class EvaluationResult:
    outcome: RuleOutcome
    matched_rule_ids: tuple[str, ...]
    rationale: str


def evaluate(
    *,
    rules: DecisionRules,
    axis_a: AxisA,
    axis_b: AxisB,
    axis_d: AxisD,
    effect_class: str,
    target: str,
    default_when_no_match: RuleOutcome = RuleOutcome.SUGGEST,
) -> EvaluationResult:
    """Evaluate rules deterministically.

    Algorithm:
    1. If no rule matches, return `default_when_no_match`. Per FR-011
       this MUST be SUGGEST or DENY — never AUTO. The default arg is
       defaulted to SUGGEST; callers may pass DENY for stricter cells.
       AUTO is rejected at runtime here (Principle VI).
    2. If rules match, compose most-restrictive across their outcomes
       (FR-026a baseline). A single rule with outcome=AUTO does NOT
       force AUTO unless it's the only matched rule.
    3. Only human-ratified rules participate (FR-014).
    """
    if default_when_no_match == RuleOutcome.AUTO:
        raise DecisionRuleError(
            "FR-011 violation: never-auto default cannot be AUTO. "
            "Pass SUGGEST or DENY as default_when_no_match.",
        )

    matched: list[DecisionRule] = []
    for rule in rules.rules:
        if rule.human_ratified_by is None:
            # FR-014 — unratified rules have zero effect. Skip silently.
            continue
        if rule.predicate.matches(
            axis_a=axis_a,
            axis_b=axis_b,
            axis_d=axis_d,
            effect_class=effect_class,
            target=target,
        ):
            matched.append(rule)

    if not matched:
        return EvaluationResult(
            outcome=default_when_no_match,
            matched_rule_ids=(),
            rationale=(f"no human-ratified rule matched; default={default_when_no_match.value}"),
        )

    composed = _most_restrictive(*(r.outcome for r in matched))
    ids = tuple(sorted(r.rule_id for r in matched))
    return EvaluationResult(
        outcome=composed,
        matched_rule_ids=ids,
        rationale=f"matched rules={list(ids)}; composed most-restrictive={composed.value}",
    )


def load(path: Path) -> DecisionRules:
    """Load configs/rules.yaml. Fail-closed on missing or unparseable.
    An empty `rules:` is permitted (yields a DecisionRules with no
    rules — the never-auto default takes effect on every evaluation)."""
    if not path.is_file():
        raise DecisionRuleError(f"rules config missing: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise DecisionRuleError(f"unparseable: {path} — {e}") from e
    if data is None:
        return DecisionRules(rules=())
    raw = data.get("rules") or []
    if not isinstance(raw, list):
        raise DecisionRuleError(f"'rules' must be a list: {path}")
    parsed: list[DecisionRule] = []
    seen_ids: set[str] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise DecisionRuleError(f"rules[{i}] is not an object")
        try:
            rid = str(item["rule_id"])
            outcome = RuleOutcome(str(item["outcome"]))
        except KeyError as e:
            raise DecisionRuleError(
                f"rules[{i}] missing required: {e.args[0]!r}",
            ) from e
        except ValueError as e:
            raise DecisionRuleError(f"rules[{i}] bad outcome: {e}") from e
        if rid in seen_ids:
            raise DecisionRuleError(f"rules[{i}] duplicate rule_id: {rid!r}")
        seen_ids.add(rid)
        when_raw = item.get("when") or {}
        if not isinstance(when_raw, dict):
            raise DecisionRuleError(f"rules[{i}].when must be a dict")
        predicate = _parse_predicate(when_raw)
        parsed.append(
            DecisionRule(
                rule_id=rid,
                predicate=predicate,
                outcome=outcome,
                rationale=str(item.get("rationale", "")),
                risk_ids=tuple(str(r) for r in (item.get("risk_ids") or [])),
                human_ratified_by=item.get("human_ratified_by"),
            ),
        )
    return DecisionRules(rules=tuple(parsed))


def _parse_predicate(when: dict[str, Any]) -> RulePredicate:
    """Build a RulePredicate from the rules.yaml `when:` clause."""
    axis_a = when.get("axis_a") or {}
    axis_b = when.get("axis_b") or {}
    axis_c = when.get("axis_c") or {}
    axis_d = when.get("axis_d") or {}
    if not isinstance(axis_a, dict) or not isinstance(axis_b, dict):
        raise DecisionRuleError("when.axis_a / axis_b must be dicts")
    if not isinstance(axis_c, dict) or not isinstance(axis_d, dict):
        raise DecisionRuleError("when.axis_c / axis_d must be dicts")
    return RulePredicate(
        axis_a_category=axis_a.get("category"),
        axis_b_provenance=axis_b.get("provenance"),
        effect_class=axis_c.get("effect_class"),
        axis_d_initiator=axis_d.get("initiator"),
        axis_d_counterparty=axis_d.get("counterparty"),
        axis_d_relationship_group_id=axis_d.get("relationship_group_id"),
        axis_d_expectedness=axis_d.get("expectedness"),
        axis_d_reversibility_degree=axis_d.get("reversibility"),
        target=when.get("target"),
    )


# --- T037/T046: asymmetry invariant (FR-031) -------------------------

# Whitelist of origins that may contribute a *relax* input (an input
# that, if accepted, would move a decision toward AUTO). Anything not
# in this set is non-deterministic from the policy engine's point of
# view — model suggestions, runtime heuristics, anomaly-detector
# confidence scores, etc. — and is refused per FR-031.
ALLOWED_RELAX_ORIGINS: frozenset[str] = frozenset(
    {
        "operator-config",
        "human-ratified-rule",
        "curated-mcp",
    },
)


@dataclass(frozen=True)
class RelaxInput:
    """A runtime-provided claim that the baseline decision should be
    relaxed (moved toward AUTO). Each carries a free-form description
    and an `origin` tag. Only `origin` values in ALLOWED_RELAX_ORIGINS
    are accepted; everything else is refused per FR-031.

    `origin` is operator/system-attached metadata, not user input —
    a model cannot forge its own origin tag because the tag is set by
    the trusted code path that constructs the RelaxInput, never by
    the model that suggested the underlying content."""

    description: str
    origin: str


def refuse_non_deterministic_relax_input(
    *,
    input_origin: str,
) -> None:
    """Raising form of the asymmetry check. Used in pure-function
    contexts where the caller wants an exception. For audit-emitting
    callers (e.g. engine.decide()), use `inspect_relax_inputs()`
    which returns structured info instead of raising."""
    if input_origin not in ALLOWED_RELAX_ORIGINS:
        raise DecisionRuleError(
            f"FR-031: relax input from non-deterministic origin "
            f"{input_origin!r} refused "
            f"(allowed: {sorted(ALLOWED_RELAX_ORIGINS)})",
        )


@dataclass(frozen=True)
class RelaxInspectionResult:
    """Outcome of inspecting a batch of RelaxInputs for FR-031 compliance.

    `refused` lists the inputs whose origin is non-deterministic.
    `accepted` lists the inputs whose origin is in
    ALLOWED_RELAX_ORIGINS. If `refused` is non-empty, callers MUST
    refuse the entire decision (FR-031 asymmetry) and emit an audit
    record capturing the refused inputs (T046).
    """

    accepted: tuple[RelaxInput, ...]
    refused: tuple[RelaxInput, ...]

    @property
    def has_refusal(self) -> bool:
        return len(self.refused) > 0


def inspect_relax_inputs(
    relax_inputs: tuple[RelaxInput, ...],
) -> RelaxInspectionResult:
    """Partition a batch of relax inputs into accepted/refused
    according to FR-031. Does NOT raise — the caller is expected to
    fold the result into a structured decision + audit event."""
    accepted = tuple(r for r in relax_inputs if r.origin in ALLOWED_RELAX_ORIGINS)
    refused = tuple(r for r in relax_inputs if r.origin not in ALLOWED_RELAX_ORIGINS)
    return RelaxInspectionResult(accepted=accepted, refused=refused)
