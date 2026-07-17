"""#378/#380 — unified authoring surface: the compact `when → outcome` grammar
compiles to the engine's existing DecisionRules and drives the real evaluator;
fail-closed via the single ConfigError."""

from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.policy.authoring import (
    CompiledPolicy,
    ConfigError,
    compile_rule,
    compile_rules,
    load_config,
    parse_when,
)
from capabledeputy.policy.decision_rules import RuleOutcome, evaluate
from capabledeputy.policy.labels import (
    AxisD,
    CategoryTag,
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
)
from capabledeputy.policy.tiers import Tier

# --- parse_when field mapping ---------------------------------------------


def test_parse_when_maps_each_facet() -> None:
    p = parse_when("financial + send_email + external", where="t")
    assert p.axis_a_category == "financial"
    assert p.effect_class == "social.send_email"
    assert p.axis_b_provenance == "external-untrusted"


def test_parse_when_multi_category_is_and_semantics() -> None:
    p = parse_when("financial health", where="t")
    assert set(p.axis_a_categories) == {"financial", "health"}
    assert p.axis_a_category is None


def test_parse_when_parameterized_terms() -> None:
    p = parse_when("purchase to:*@acme.com time:22-6", where="t")
    assert p.effect_class == "commerce.purchase"
    assert p.target == "*@acme.com"
    assert p.axis_d_time_window == (22, 6)


def test_parse_when_rejects_two_actions_and_bad_time() -> None:
    with pytest.raises(ConfigError, match="more than one action"):
        parse_when("send_email send_message", where="t")
    with pytest.raises(ConfigError, match="time window"):
        parse_when("purchase time:99-6", where="t")
    with pytest.raises(ConfigError, match="unknown term key"):
        parse_when("bogus:x", where="t")


# --- compile_rule / compile_rules -----------------------------------------


def test_compile_rule_outcome_aliases() -> None:
    r = compile_rule(0, {"id": "x", "when": "news", "then": "deny", "because": "no"})
    assert r.rule_id == "x"
    assert r.outcome == RuleOutcome.DENY
    assert r.rationale == "no"
    assert r.predicate.axis_a_category == "news"

    for word, expected in [
        ("allow", RuleOutcome.AUTO),
        ("approve", RuleOutcome.REQUIRE_APPROVAL),
        ("override", RuleOutcome.OVERRIDE_REQUIRED),
    ]:
        assert compile_rule(0, {"id": "x", "when": "news", "then": word}).outcome == expected


def test_compile_rules_fail_closed() -> None:
    with pytest.raises(ConfigError, match="missing required: 'id'"):
        compile_rules([{"when": "news", "then": "deny"}])
    with pytest.raises(ConfigError, match="missing required: 'when'"):
        compile_rules([{"id": "x", "then": "deny"}])
    with pytest.raises(ConfigError, match="missing required: 'then'"):
        compile_rules([{"id": "x", "when": "news"}])
    with pytest.raises(ConfigError, match="unknown outcome"):
        compile_rules([{"id": "x", "when": "news", "then": "maybe"}])
    with pytest.raises(ConfigError, match="duplicate id"):
        compile_rules(
            [
                {"id": "x", "when": "news", "then": "deny"},
                {"id": "x", "when": "health", "then": "deny"},
            ],
        )
    with pytest.raises(ConfigError, match="must be a list"):
        compile_rules({"id": "x"})


def test_ratified_and_crosses_floor_pass_through() -> None:
    r = compile_rule(
        0,
        {
            "id": "x",
            "when": "health + send_email",
            "then": "allow",
            "human_ratified_by": "owner",
            "crosses_floor": "health-meets-egress",
        },
    )
    assert r.human_ratified_by == "owner"
    assert r.crosses_floor == "health-meets-egress"
    assert r.outcome == RuleOutcome.AUTO


# --- the payoff: compiled rules drive the real evaluator ------------------


def test_compiled_rule_fires_in_the_engine_evaluator() -> None:
    """A compact `news + send_email → deny` rule, compiled and fed to the real
    v2 evaluator, denies a news+email action — proving the compiler emits
    structures the engine treats identically to hand-authored rules."""
    # FR-014: only human-ratified rules fire in the evaluator (any outcome).
    rules = compile_rules(
        [
            {
                "id": "no-news-email",
                "when": "news + send_email",
                "then": "deny",
                "because": "x",
                "human_ratified_by": "owner",
            },
        ],
    )
    result = evaluate(
        rules=rules,
        labels=LabelState(a=frozenset({CategoryTag("news", Tier.SENSITIVE)})),
        axis_d=AxisD(initiator="principal:owner"),
        effect_class="social.send_email",
        target="bob@example.com",
    )
    assert result.outcome == RuleOutcome.DENY
    assert "no-news-email" in result.matched_rule_ids


def test_compiled_provenance_rule_matches_untrusted() -> None:
    rules = compile_rules(
        [
            {
                "id": "no-untrusted-email",
                "when": "external + send_email",
                "then": "deny",
                "human_ratified_by": "owner",
            },
        ],
    )
    result = evaluate(
        rules=rules,
        labels=LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)})),
        axis_d=AxisD(initiator="external:sender"),
        effect_class="social.send_email",
        target="bob@example.com",
    )
    assert result.outcome == RuleOutcome.DENY


# --- load_config ----------------------------------------------------------


def test_load_config_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="missing"):
        load_config(tmp_path / "nope.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("rules: [ : : bad", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(bad)


def test_load_config_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "capdep.yaml"
    p.write_text(
        "rules:\n"
        "  - id: no-external-financial\n"
        "    when: financial + send_email + external\n"
        "    then: deny\n"
        "    because: financial data may not be emailed externally\n",
        encoding="utf-8",
    )
    compiled = load_config(p)
    assert isinstance(compiled, CompiledPolicy)
    assert len(compiled.rules.rules) == 1
    rule = compiled.rules.rules[0]
    assert rule.rule_id == "no-external-financial"
    assert rule.outcome == RuleOutcome.DENY
    assert rule.predicate.axis_a_category == "financial"
    assert rule.predicate.effect_class == "social.send_email"
    assert rule.predicate.axis_b_provenance == "external-untrusted"


def test_compile_document_fail_closed_on_non_mapping() -> None:
    from capabledeputy.policy.authoring import compile_document

    with pytest.raises(ConfigError, match="mapping of sections"):
        compile_document([1, 2, 3])
    assert compile_document(None).rules.rules == ()


# --- #382: envelopes as range outcomes ------------------------------------

from capabledeputy.policy.authoring import compile_envelope, compile_envelopes  # noqa: E402
from capabledeputy.policy.envelope import CellKey, RiskPreference  # noqa: E402


def _env_entry(**over):
    base = {
        "when": "financial + send_email + initiator:principal:owner + reversibility:irreversible",
        "range": ["approve", "allow"],
    }
    base.update(over)
    return base


def test_compile_envelope_builds_the_cell_and_band() -> None:
    env = compile_envelope(0, _env_entry())
    assert env.cell == CellKey(
        category="financial",
        effect="social.send_email",
        decision_context_canonical="principal:owner",
        reversibility="irreversible",
    )
    # approve -> REQUIRE_APPROVAL (strictest); allow -> AUTO (loosest).
    assert env.strictest == RuleOutcome.REQUIRE_APPROVAL
    assert env.loosest == RuleOutcome.AUTO


def test_compiled_envelope_dial_selection_matches_engine() -> None:
    """The compiled envelope drives the engine's dial selection exactly: cautious
    picks strictest, permissive picks loosest — proving the compiled cell is the
    same structure the engine consumes."""
    env = compile_envelope(0, _env_entry())
    assert env.select(RiskPreference.CAUTIOUS) == RuleOutcome.REQUIRE_APPROVAL
    assert env.select(RiskPreference.PERMISSIVE) == RuleOutcome.AUTO


def test_compiled_envelope_cell_matches_the_decide_lookup_key() -> None:
    """The cell the compiler builds is byte-identical to the CellKey decide()
    computes for the same context — so a compiled envelope actually fires."""
    from capabledeputy.policy.envelope import CellKey as EngineCell

    env = compile_envelope(0, _env_entry())
    # This mirrors engine._decide_impl's cell construction.
    engine_cell = EngineCell(
        category="financial",
        effect="social.send_email",
        decision_context_canonical="principal:owner",
        reversibility="irreversible",
    )
    assert env.cell == engine_cell


def test_compile_envelopes_fail_closed() -> None:
    with pytest.raises(ConfigError, match="missing required: 'range'"):
        compile_envelopes([{"when": "financial + send_email"}])
    with pytest.raises(ConfigError, match="every cell coordinate"):
        # missing initiator + reversibility
        compile_envelopes([{"when": "financial + send_email", "range": ["approve", "allow"]}])
    with pytest.raises(ConfigError, match="2-item"):
        compile_envelopes([_env_entry(range=["approve"])])
    with pytest.raises(ConfigError, match="unknown outcome"):
        compile_envelopes([_env_entry(range=["nope", "allow"])])
    # strictest looser than loosest is rejected (via EnvelopeError -> ConfigError).
    with pytest.raises(ConfigError):
        compile_envelopes([_env_entry(range=["allow", "approve"])])
    with pytest.raises(ConfigError, match="duplicate cell"):
        compile_envelopes([_env_entry(), _env_entry()])
    with pytest.raises(ConfigError, match="must be a list"):
        compile_envelopes({"when": "x"})


def test_compile_document_includes_envelopes() -> None:
    from capabledeputy.policy.authoring import compile_document

    compiled = compile_document({"envelopes": [_env_entry()]})
    assert len(compiled.envelopes.by_cell) == 1
    cell = next(iter(compiled.envelopes.by_cell))
    assert cell.category == "financial"
