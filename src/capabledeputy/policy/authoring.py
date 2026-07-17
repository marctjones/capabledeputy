"""Unified policy authoring surface + compiler (#378, #380).

`docs/policy-authoring-design.md` §4/§9: everything a human authors is the same
sentence — `when <match> → <outcome>` — and one schema-driven loader *compiles*
that surface down to the engine's existing typed structures. The engine is
untouched; this is the authoring layer that "specializes internally."

This module lands the compiler machinery and proves it end-to-end on the
**Rules** concept (the most central one): a compact `when` expression compiles to
the same `DecisionRules` the verbose `configs/rules.yaml` grammar produces, so a
human writes

    rules:
      - id: no-external-financial
        when: financial + send_email + external
        then: deny
        because: financial data may not be emailed to an external recipient

instead of a nested `axis_a/axis_b/axis_c` block. Folding the remaining concepts
(labels, envelopes, posture) onto this same surface is Phase 2 (#381-#383).

Fail-closed (#380 / Principle VI): all parse/compile errors are the single
`ConfigError` — no per-file error types, one uniform failure contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from capabledeputy.policy.decision_rules import (
    DecisionRule,
    DecisionRules,
    RuleOutcome,
    RulePredicate,
)
from capabledeputy.policy.envelope import (
    CellKey,
    EnvelopeError,
    EnvelopeSet,
    OutcomeEnvelope,
)


class ConfigError(RuntimeError):
    """The single fail-closed error for the unified authoring surface (#380).

    Replaces the ~26 per-file `*Error` types with one uniform contract: any
    missing / unparseable / invalid config refuses (Principle VI), with a stable
    message shape `<section>[<index>]: <what>`."""


# --- compact `when` vocabulary --------------------------------------------
#
# A `when` expression is AND-combined terms separated by `+` or whitespace.
# Each term maps to exactly one predicate facet. The vocabulary is a FIXED,
# closed set (design §6 level 1) so every rule stays analyzable — no free-form
# logic. `key:value` terms carry the few parameterized facets.

# Friendly action name → the effect_class string the engine matches on.
_EFFECT_TERMS: dict[str, str] = {
    "send_email": "social.send_email",
    "send_message": "social.send_message",
    "purchase": "commerce.purchase",
    "queue_purchase": "commerce.purchase",
    "web_fetch": "data.fetch",
    "read_file": "data.read_file",
    "delete": "storage.delete",
}
# Provenance terms → the Axis-B provenance level string.
_PROVENANCE_TERMS: dict[str, str] = {
    "external": "external-untrusted",
    "untrusted": "external-untrusted",
    "external-untrusted": "external-untrusted",
}
# Friendly outcome aliases → the engine's RuleOutcome. Both the friendly word
# and the canonical value are accepted.
_OUTCOME_ALIASES: dict[str, RuleOutcome] = {
    "allow": RuleOutcome.AUTO,
    "auto": RuleOutcome.AUTO,
    "deny": RuleOutcome.DENY,
    "approve": RuleOutcome.REQUIRE_APPROVAL,
    "require-approval": RuleOutcome.REQUIRE_APPROVAL,
    "override": RuleOutcome.OVERRIDE_REQUIRED,
    "override-required": RuleOutcome.OVERRIDE_REQUIRED,
    "suggest": RuleOutcome.SUGGEST,
    "shadow": RuleOutcome.SHADOW,
}


def _parse_time_window(value: str, where: str) -> tuple[int, int]:
    parts = value.split("-")
    if len(parts) != 2:
        raise ConfigError(f"{where}: time window must be 'HH-HH', got {value!r}")
    try:
        start, end = int(parts[0]), int(parts[1])
    except ValueError as e:
        raise ConfigError(f"{where}: time window hours must be integers: {value!r}") from e
    for h in (start, end):
        if not 0 <= h <= 23:
            raise ConfigError(f"{where}: time window hour {h} out of range 0-23")
    return (start, end)


def parse_when(expr: str, *, where: str) -> RulePredicate:
    """Compile a compact `when` expression into a `RulePredicate`.

    Terms (AND-combined, `+`/whitespace separated):
      - a known action word (`send_email`, `purchase`, `web_fetch`, …) → effect
      - `external` / `untrusted` → Axis-B external-untrusted provenance
      - `to:<glob>` → an exact target match
      - `time:HH-HH` → an Axis-D time window (UTC)
      - anything else → an Axis-A category (multiple ⇒ AND-semantics)
    """
    if not isinstance(expr, str) or not expr.strip():
        raise ConfigError(f"{where}: 'when' must be a non-empty expression")
    terms = [t for t in expr.replace("+", " ").split() if t]
    categories: list[str] = []
    provenance: str | None = None
    effect_class: str | None = None
    target: str | None = None
    time_window: tuple[int, int] | None = None
    initiator: str | None = None
    reversibility: str | None = None

    for term in terms:
        low = term.lower()
        if ":" in term:
            key, _, val = term.partition(":")
            key = key.lower()
            if key == "to":
                target = val
            elif key == "time":
                time_window = _parse_time_window(val, where)
            elif key == "category":
                categories.append(val)
            elif key == "effect":
                effect_class = val
            elif key == "initiator":
                initiator = val
            elif key == "reversibility":
                reversibility = val
            else:
                raise ConfigError(f"{where}: unknown term key {key!r} in 'when'")
            continue
        if low in _EFFECT_TERMS:
            if effect_class is not None:
                raise ConfigError(f"{where}: more than one action term in 'when'")
            effect_class = _EFFECT_TERMS[low]
        elif low in _PROVENANCE_TERMS:
            provenance = _PROVENANCE_TERMS[low]
        else:
            categories.append(low)

    predicate_kwargs: dict = {}
    if len(categories) == 1:
        predicate_kwargs["axis_a_category"] = categories[0]
    elif categories:
        predicate_kwargs["axis_a_categories"] = tuple(categories)
    if provenance is not None:
        predicate_kwargs["axis_b_provenance"] = provenance
    if effect_class is not None:
        predicate_kwargs["effect_class"] = effect_class
    if target is not None:
        predicate_kwargs["target"] = target
    if time_window is not None:
        predicate_kwargs["axis_d_time_window"] = time_window
    if initiator is not None:
        predicate_kwargs["axis_d_initiator"] = initiator
    if reversibility is not None:
        predicate_kwargs["axis_d_reversibility_degree"] = reversibility
    if not predicate_kwargs:
        raise ConfigError(f"{where}: 'when' matched no facets (empty predicate)")
    return RulePredicate(**predicate_kwargs)


def compile_rule(index: int, raw: object) -> DecisionRule:
    """Compile one compact rule entry into a `DecisionRule`."""
    where = f"rules[{index}]"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} is not an object")
    try:
        rid = str(raw["id"])
    except KeyError:
        raise ConfigError(f"{where} missing required: 'id'") from None
    if "when" not in raw:
        raise ConfigError(f"{where} ({rid!r}) missing required: 'when'")
    if "then" not in raw:
        raise ConfigError(f"{where} ({rid!r}) missing required: 'then'")
    predicate = parse_when(raw["when"], where=f"{where} ({rid!r})")
    then = str(raw["then"]).lower()
    if then not in _OUTCOME_ALIASES:
        raise ConfigError(
            f"{where} ({rid!r}): unknown outcome {then!r}; one of {sorted(_OUTCOME_ALIASES)}",
        )
    return DecisionRule(
        rule_id=rid,
        predicate=predicate,
        outcome=_OUTCOME_ALIASES[then],
        rationale=str(raw.get("because", "")),
        risk_ids=tuple(str(r) for r in (raw.get("risk_ids") or [])),
        human_ratified_by=raw.get("human_ratified_by"),
        crosses_floor=(str(raw["crosses_floor"]) if raw.get("crosses_floor") else None),
    )


def compile_rules(section: object) -> DecisionRules:
    """Compile the `rules:` section (list of compact entries) into the engine's
    `DecisionRules`. Empty/missing ⇒ no rules (never-auto default)."""
    if section is None:
        return DecisionRules(rules=())
    if not isinstance(section, list):
        raise ConfigError("rules: must be a list")
    out: list[DecisionRule] = []
    seen: set[str] = set()
    for i, raw in enumerate(section):
        rule = compile_rule(i, raw)
        if rule.rule_id in seen:
            raise ConfigError(f"rules[{i}]: duplicate id {rule.rule_id!r}")
        seen.add(rule.rule_id)
        out.append(rule)
    return DecisionRules(rules=tuple(out))


# --- envelopes: a rule whose outcome is a RANGE (#382) --------------------
#
# design §4: "an envelope is just a rule whose outcome is a range the dial picks
# within." The compact `when` supplies the four cell coordinates (category,
# effect, initiator, reversibility); `range: [strictest, loosest]` supplies the
# band. Compiles to the engine's existing OutcomeEnvelope / EnvelopeSet.


def _outcome(word: str, where: str) -> RuleOutcome:
    low = str(word).lower()
    if low not in _OUTCOME_ALIASES:
        raise ConfigError(f"{where}: unknown outcome {word!r}; one of {sorted(_OUTCOME_ALIASES)}")
    return _OUTCOME_ALIASES[low]


def compile_envelope(index: int, raw: object) -> OutcomeEnvelope:
    """Compile one compact envelope entry into an `OutcomeEnvelope`. The cell key
    requires all four coordinates so the engine's exact-match lookup can fire."""
    where = f"envelopes[{index}]"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} is not an object")
    if "when" not in raw:
        raise ConfigError(f"{where} missing required: 'when'")
    if "range" not in raw:
        raise ConfigError(f"{where} missing required: 'range' [strictest, loosest]")
    predicate = parse_when(raw["when"], where=where)
    missing = [
        name
        for name, val in (
            ("category", predicate.axis_a_category),
            ("effect", predicate.effect_class),
            ("initiator", predicate.axis_d_initiator),
            ("reversibility", predicate.axis_d_reversibility_degree),
        )
        if val is None
    ]
    if missing:
        raise ConfigError(
            f"{where}: envelope 'when' must set every cell coordinate; missing {missing} "
            "(need a single category + effect:/initiator:/reversibility:)",
        )
    band = raw["range"]
    if not isinstance(band, list) or len(band) != 2:
        raise ConfigError(f"{where}: 'range' must be a 2-item [strictest, loosest] list")
    strictest = _outcome(band[0], where)
    loosest = _outcome(band[1], where)
    cell = CellKey(
        category=predicate.axis_a_category,
        effect=predicate.effect_class,
        decision_context_canonical=predicate.axis_d_initiator,
        reversibility=predicate.axis_d_reversibility_degree,
    )
    try:
        return OutcomeEnvelope(cell=cell, strictest=strictest, loosest=loosest)
    except EnvelopeError as e:
        # strictest-looser-than-loosest etc. Re-raise as the uniform ConfigError.
        raise ConfigError(f"{where}: {e}") from e


def compile_envelopes(section: object) -> EnvelopeSet:
    """Compile the `envelopes:` section into the engine's `EnvelopeSet`."""
    if section is None:
        return EnvelopeSet(by_cell={})
    if not isinstance(section, list):
        raise ConfigError("envelopes: must be a list")
    by_cell: dict[CellKey, OutcomeEnvelope] = {}
    for i, raw in enumerate(section):
        env = compile_envelope(i, raw)
        if env.cell in by_cell:
            raise ConfigError(f"envelopes[{i}]: duplicate cell {env.cell}")
        by_cell[env.cell] = env
    return EnvelopeSet(by_cell=by_cell)


@dataclass(frozen=True)
class CompiledPolicy:
    """The compiled output of the unified authoring surface. Grows a field per
    concept as Phase 2 folds them in; today it carries the compiled decision
    rules and outcome envelopes."""

    rules: DecisionRules = field(default_factory=lambda: DecisionRules(rules=()))
    envelopes: EnvelopeSet = field(default_factory=lambda: EnvelopeSet(by_cell={}))


def compile_document(doc: object) -> CompiledPolicy:
    """Compile a parsed unified document (a mapping of sections) to typed engine
    structures. Fail-closed on a non-mapping root."""
    if doc is None:
        return CompiledPolicy()
    if not isinstance(doc, dict):
        raise ConfigError("policy document root must be a mapping of sections")
    return CompiledPolicy(
        rules=compile_rules(doc.get("rules")),
        envelopes=compile_envelopes(doc.get("envelopes")),
    )


def load_config(path: Path) -> CompiledPolicy:
    """Load + compile a unified `capdep.yaml`-style policy document. Fail-closed
    (single `ConfigError`) on a missing or unparseable file — the uniform
    contract that replaces the per-file loaders/errors (#380)."""
    if not path.is_file():
        raise ConfigError(f"policy config missing: {path}")
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"policy config unparseable: {path} — {e}") from e
    return compile_document(doc)
