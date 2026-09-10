"""Regression: configs/rules.yaml's declared `axis_c.effect_class`
values must actually match what the REAL ToolRegistry's native tools
declare on `ToolDefinition.effect_class` -- otherwise a rule is dead
code against production dispatch. The v2 rules leg only engages using
`tool.effect_class` as the match key (see
`LabeledToolClient._build_v2_decide_kwargs` / `RulePredicate.matches`).

The hand-typed evaluator tests in test_example_rules_yaml.py construct
synthetic `effect_class` strings by hand and never touch the real
ToolRegistry, so a drift between rules.yaml and the tools' declared
strings (e.g. bare "send_email" vs the real "social.send_email") is
invisible to them. These tests go through the real `App` (real
registry, real tool_client dispatch, real configs/rules.yaml) instead.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from capabledeputy.app import App
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.context import PolicyContext
from capabledeputy.policy.decision_rules import load
from capabledeputy.policy.labels import AxisD, CategoryTag, LabelState
from capabledeputy.policy.purchase_reversibility import parse_purchase_reversibility_policy
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier
from capabledeputy.session.model import Session

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RULES_YAML = _REPO_ROOT / "configs" / "rules.yaml"

# axis_c.effect_class values referenced in configs/rules.yaml that
# don't (yet) correspond to any registered tool: documented US2
# scenario fixtures for tools that aren't implemented (see the file's
# own comments on cron-backup-auto / proprietary-share-to-project-p).
# Confirm before adding to this set -- it's meant to be adjusted only
# when a value is genuinely a forward-looking fixture, not to paper
# over a real mismatch.
_KNOWN_FIXTURE_ONLY_EFFECTS = {"backup_storage", "share"}


@pytest.fixture(autouse=True)
def _pin_business_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests go through the real `call_tool` dispatch chokepoint,
    which stamps `dispatch_now = datetime.now(UTC)` with no test hook
    (unlike `decide()`'s `now_hour=` used in test_example_rules_yaml.py
    / test_policy_language_gaps.py). Left unpinned, a run during
    22:00-06:00 UTC hits `send-after-hours-require-approval` and
    corrupts assertions (like `outcome.rule == "v2:default"`) that have
    nothing to do with time-of-day. Pin to a fixed business-hours
    instant so these tests assert only what they claim to."""
    fixed_now = datetime(2026, 1, 6, 14, 0, tzinfo=UTC)  # Tuesday, 14:00 UTC

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return fixed_now if tz is None else fixed_now.astimezone(tz)

    monkeypatch.setattr("capabledeputy.tools.client.datetime", _FixedDatetime)


@pytest.fixture
async def app(tmp_path: Path) -> App:
    a = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        policy_context=PolicyContext(rules_v2=load(_RULES_YAML)),
    )
    await a.startup()
    return a


@pytest.fixture
async def app_with_amazon_reversibility_policy(tmp_path: Path) -> App:
    """Same as `app`, plus an operator-declared vendor allowlist
    treating "amazon*" purchases as reversible/system -- the wiring
    that lets `purchases-under-threshold-auto` actually reach ALLOW
    (see policy/purchase_reversibility.py)."""
    policy = parse_purchase_reversibility_policy(
        {
            "purchase_reversibility": [
                {
                    "match": {"vendor_glob": "amazon*"},
                    "reversibility": {"degree": "reversible", "agent": "system"},
                },
            ],
        },
    )
    a = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        policy_context=PolicyContext(rules_v2=load(_RULES_YAML)),
        purchase_reversibility_policy=policy,
    )
    await a.startup()
    return a


def test_every_rules_yaml_effect_class_matches_a_real_tool_or_known_fixture(
    app: App,
) -> None:
    """Every axis_c.effect_class value declared in configs/rules.yaml
    must equal the `ToolDefinition.effect_class` some registered tool
    actually declares -- or be an explicitly acknowledged fixture for
    a not-yet-implemented tool. Anything else means the rule can never
    match a real dispatch."""
    real_effect_classes = {
        t.effect_class for t in app.registry.list() if t.effect_class is not None
    }
    rules = load(_RULES_YAML)
    declared = {
        r.predicate.effect_class for r in rules.rules if r.predicate.effect_class is not None
    }
    unaccounted = declared - real_effect_classes - _KNOWN_FIXTURE_ONLY_EFFECTS
    assert not unaccounted, (
        "rules.yaml declares effect_class values with no matching registered "
        f"tool and no fixture allowlist entry: {sorted(unaccounted)}"
    )


def _personal_label_state() -> LabelState:
    return LabelState(
        a=frozenset(
            {CategoryTag("personal", Tier.SENSITIVE, assignment_provenance="human-declared")},
        ),
    )


async def test_real_email_send_to_family_hits_family_personal_email_suggest_rule(
    app: App,
) -> None:
    """Dispatching the REAL email.send tool (whose ToolDefinition
    declares effect_class='social.send_email') with axis_a=personal
    and a `family` relationship group must resolve via the
    human-ratified `family-personal-email-suggest` rule -- not the
    never-auto default -- proving the v2 rule actually fires against a
    real dispatch through the real registry. Both the matched and
    default cases land on REQUIRE_APPROVAL (irreversible egress always
    gates); what distinguishes them is which rule the decision names."""
    session = await app.graph.new(intent="test-email-family")
    cap = Capability(kind=CapabilityKind.SEND_EMAIL, pattern="*")
    app.graph._sessions[session.id] = replace(
        session,
        capability_set=frozenset({cap}),
        label_state=_personal_label_state(),
        axis_d=AxisD(
            initiator="principal:alice",
            authentication="device-bound",
            expectedness="expected",
            relationship_group_ids=frozenset({"family"}),
            reversibility={"degree": "irreversible", "agent": "external"},
        ),
    )

    outcome = await app.tool_client.call_tool(
        session.id,
        "email.send",
        {"to": "spouse@example.com", "subject": "hi", "body": "hi"},
    )

    assert outcome.rule is not None
    assert "family-personal-email-suggest" in outcome.rule


async def test_real_email_send_to_non_family_falls_to_v2_default(
    app: App,
) -> None:
    """Same real dispatch, no recognized relationship group -- the
    rule must NOT match, falling to the `v2:default` never-auto cell.
    Contrast with the family case above: proves the rule is actually
    selective, not matching everything by accident."""
    session = await app.graph.new(intent="test-email-non-family")
    cap = Capability(kind=CapabilityKind.SEND_EMAIL, pattern="*")
    app.graph._sessions[session.id] = replace(
        session,
        capability_set=frozenset({cap}),
        label_state=_personal_label_state(),
        axis_d=AxisD(
            initiator="principal:alice",
            authentication="device-bound",
            expectedness="expected",
            reversibility={"degree": "irreversible", "agent": "external"},
        ),
    )

    outcome = await app.tool_client.call_tool(
        session.id,
        "email.send",
        {"to": "stranger@example.com", "subject": "hi", "body": "hi"},
    )

    assert outcome.rule == "v2:default"


async def _purchase_session(app: App) -> Session:
    session = await app.graph.new(intent="test-purchase")
    cap = Capability(kind=CapabilityKind.QUEUE_PURCHASE, pattern="*", max_amount=10_000)
    updated = replace(
        session,
        capability_set=frozenset({cap}),
        axis_d=AxisD(
            initiator="principal:alice",
            authentication="device-bound",
            expectedness="expected",
            reversibility={"degree": "reversible", "agent": "system"},
        ),
    )
    app.graph._sessions[session.id] = updated
    return updated


async def test_real_purchase_queue_with_no_vendor_policy_still_denies(
    app: App,
) -> None:
    """Baseline / back-compat: with no purchase_reversibility_policy
    wired (today's default), a real purchase.queue dispatch keeps
    hitting the static irreversible/external floor -- DENY -- exactly
    as before this fix. `purchases-under-threshold-auto` in
    rules.yaml is real but stays inert until the operator declares a
    vendor entry."""
    session = await _purchase_session(app)
    outcome = await app.tool_client.call_tool(
        session.id,
        "purchase.queue",
        {"vendor": "amazon", "item": "socks", "amount": 10},
    )
    assert outcome.decision == Decision.DENY


async def test_real_purchase_queue_with_matching_vendor_policy_hits_auto_rule(
    app_with_amazon_reversibility_policy: App,
) -> None:
    """With an operator-declared "amazon*" entry in
    purchase_reversibility.yaml, a REAL purchase.queue dispatch to
    "amazon" now resolves reversible/system per-call, clears the
    reversibility gate, and reaches ALLOW via the human-ratified
    `purchases-under-threshold-auto` rule -- proving the resolver
    actually unblocks the previously-dead AUTO rule end-to-end."""
    app = app_with_amazon_reversibility_policy
    session = await _purchase_session(app)
    outcome = await app.tool_client.call_tool(
        session.id,
        "purchase.queue",
        {"vendor": "amazon", "item": "socks", "amount": 10},
    )
    assert outcome.decision == Decision.ALLOW


async def test_real_purchase_queue_non_matching_vendor_still_denies_under_policy(
    app_with_amazon_reversibility_policy: App,
) -> None:
    """Same policy wired, but a vendor the operator never allowlisted
    -- the static floor still holds for it; the policy is additive,
    not a blanket relaxation."""
    app = app_with_amazon_reversibility_policy
    session = await _purchase_session(app)
    outcome = await app.tool_client.call_tool(
        session.id,
        "purchase.queue",
        {"vendor": "ticketmaster", "item": "concert", "amount": 10},
    )
    assert outcome.decision == Decision.DENY
