"""Tests for the DecisionInspector port + composition + builtins."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from capabledeputy.policy.rules import Decision
from capabledeputy.substrate.decision_inspector_port import (
    DecisionRelax,
    DecisionTighten,
    compose_inspector_outcomes,
    is_strictly_less_restrictive,
    is_strictly_more_restrictive,
)

# ---------- ordering helpers ----------


def test_restrictiveness_ordering() -> None:
    """ALLOW < REQUIRE_APPROVAL < OVERRIDE_REQUIRED < DENY."""
    assert is_strictly_more_restrictive(Decision.REQUIRE_APPROVAL, Decision.ALLOW)
    assert is_strictly_more_restrictive(Decision.DENY, Decision.REQUIRE_APPROVAL)
    assert is_strictly_more_restrictive(Decision.OVERRIDE_REQUIRED, Decision.REQUIRE_APPROVAL)
    assert not is_strictly_more_restrictive(Decision.ALLOW, Decision.DENY)
    assert is_strictly_less_restrictive(Decision.ALLOW, Decision.REQUIRE_APPROVAL)
    assert is_strictly_less_restrictive(Decision.REQUIRE_APPROVAL, Decision.DENY)


# ---------- composition semantics ----------


def test_compose_no_outcomes_returns_none() -> None:
    assert compose_inspector_outcomes(Decision.ALLOW, []) is None


def test_compose_all_none_returns_none() -> None:
    outcomes: list[tuple[str, DecisionRelax | DecisionTighten | None]] = [("a", None), ("b", None)]
    assert compose_inspector_outcomes(Decision.ALLOW, outcomes) is None


def test_compose_single_relax_wins() -> None:
    outcomes: list[tuple[str, DecisionRelax | DecisionTighten | None]] = [
        ("self-egress", DecisionRelax(to=Decision.ALLOW, rule="self", rationale="ok")),
    ]
    result = compose_inspector_outcomes(Decision.REQUIRE_APPROVAL, outcomes)
    assert result is not None
    decision, rule, rationale = result
    assert decision == Decision.ALLOW
    assert rule == "self-egress:self"
    assert rationale == "ok"


def test_compose_single_tighten_wins() -> None:
    outcomes: list[tuple[str, DecisionRelax | DecisionTighten | None]] = [
        ("after-hours", DecisionTighten(to=Decision.REQUIRE_APPROVAL, rule="ah", rationale="late")),
    ]
    result = compose_inspector_outcomes(Decision.ALLOW, outcomes)
    assert result is not None
    decision, rule, _ = result
    assert decision == Decision.REQUIRE_APPROVAL
    assert rule == "after-hours:ah"


def test_compose_tighten_beats_relax() -> None:
    """The whole point: when one inspector wants to loosen and another
    wants to tighten, the tighten wins (most-restrictive composition)."""
    outcomes: list[tuple[str, DecisionRelax | DecisionTighten | None]] = [
        ("relax-er", DecisionRelax(to=Decision.ALLOW, rule="r", rationale="")),
        ("tighten-er", DecisionTighten(to=Decision.REQUIRE_APPROVAL, rule="t", rationale="")),
    ]
    result = compose_inspector_outcomes(Decision.ALLOW, outcomes)
    assert result is not None
    decision, rule, _ = result
    assert decision == Decision.REQUIRE_APPROVAL
    assert rule == "tighten-er:t"


def test_compose_strictest_tighten_wins_among_tightens() -> None:
    outcomes: list[tuple[str, DecisionRelax | DecisionTighten | None]] = [
        ("a", DecisionTighten(to=Decision.REQUIRE_APPROVAL, rule="x", rationale="")),
        ("b", DecisionTighten(to=Decision.DENY, rule="y", rationale="")),
    ]
    result = compose_inspector_outcomes(Decision.ALLOW, outcomes)
    assert result is not None
    decision, rule, _ = result
    assert decision == Decision.DENY
    assert rule == "b:y"


def test_compose_loosest_relax_wins_among_relaxes() -> None:
    outcomes: list[tuple[str, DecisionRelax | DecisionTighten | None]] = [
        ("a", DecisionRelax(to=Decision.REQUIRE_APPROVAL, rule="x", rationale="")),
        ("b", DecisionRelax(to=Decision.ALLOW, rule="y", rationale="")),
    ]
    result = compose_inspector_outcomes(Decision.DENY, outcomes)
    assert result is not None
    decision, _, _ = result
    assert decision == Decision.ALLOW


def test_compose_non_monotone_outcome_ignored() -> None:
    """A 'relax' to a stricter decision (or 'tighten' to a looser one)
    is rejected at composition — these are protocol violations."""
    # Relax claiming to lower restriction, but pointing at DENY (stricter)
    outcomes: list[tuple[str, DecisionRelax | DecisionTighten | None]] = [
        ("bad", DecisionRelax(to=Decision.DENY, rule="x", rationale="")),
    ]
    result = compose_inspector_outcomes(Decision.ALLOW, outcomes)
    assert result is None


# ---------- builtins ----------


@dataclass
class _FakeAction:
    """Minimal action shim for builtin tests."""

    kind: object
    target: str


@dataclass
class _FakeKind:
    value: str


@dataclass
class _FakeDecision:
    decision: Decision


def test_self_egress_relaxer_applies_to_own_address() -> None:
    from capabledeputy.substrate.decision_inspectors_builtin import SelfEgressRelaxer

    inspector = SelfEgressRelaxer(
        self_addresses=frozenset({"marc@example.com", "me@personal.example"}),
    )
    action = _FakeAction(kind=_FakeKind("SEND_EMAIL"), target="marc@example.com")
    result = inspector.inspect(
        action=action,
        session=None,
        proposed_outcome=_FakeDecision(decision=Decision.REQUIRE_APPROVAL),
    )
    assert isinstance(result, DecisionRelax)
    assert result.to == Decision.ALLOW


def test_self_egress_relaxer_skips_external_addresses() -> None:
    from capabledeputy.substrate.decision_inspectors_builtin import SelfEgressRelaxer

    inspector = SelfEgressRelaxer(self_addresses=frozenset({"marc@example.com"}))
    action = _FakeAction(kind=_FakeKind("SEND_EMAIL"), target="someone-else@example.com")
    result = inspector.inspect(
        action=action,
        session=None,
        proposed_outcome=_FakeDecision(decision=Decision.REQUIRE_APPROVAL),
    )
    assert result is None


def test_self_egress_relaxer_skips_wrong_action_kind() -> None:
    from capabledeputy.substrate.decision_inspectors_builtin import SelfEgressRelaxer

    inspector = SelfEgressRelaxer(self_addresses=frozenset({"marc@example.com"}))
    action = _FakeAction(kind=_FakeKind("READ_FS"), target="marc@example.com")
    result = inspector.inspect(
        action=action,
        session=None,
        proposed_outcome=_FakeDecision(decision=Decision.REQUIRE_APPROVAL),
    )
    assert result is None


def test_self_egress_relaxer_skips_already_allowed() -> None:
    """Only relax REQUIRE_APPROVAL → ALLOW. Don't touch other states."""
    from capabledeputy.substrate.decision_inspectors_builtin import SelfEgressRelaxer

    inspector = SelfEgressRelaxer(self_addresses=frozenset({"marc@example.com"}))
    action = _FakeAction(kind=_FakeKind("SEND_EMAIL"), target="marc@example.com")
    result = inspector.inspect(
        action=action,
        session=None,
        proposed_outcome=_FakeDecision(decision=Decision.ALLOW),
    )
    assert result is None


def test_after_hours_tightener_in_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Freeze the clock inside the after-hours window."""
    from datetime import UTC, datetime
    from unittest.mock import patch

    from capabledeputy.substrate.decision_inspectors_builtin import (
        AfterHoursPurchaseTightener,
    )

    inspector = AfterHoursPurchaseTightener(start_hour_utc=22, end_hour_utc=6)
    action = _FakeAction(kind=_FakeKind("QUEUE_PURCHASE"), target="amazon.com")

    frozen_time = datetime(2026, 5, 21, 2, 0, 0, tzinfo=UTC)  # 2 AM UTC
    with patch("capabledeputy.substrate.decision_inspectors_builtin.datetime") as mock_dt:
        mock_dt.now.return_value = frozen_time
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        result = inspector.inspect(
            action=action,
            session=None,
            proposed_outcome=_FakeDecision(decision=Decision.ALLOW),
        )
    assert isinstance(result, DecisionTighten)
    assert result.to == Decision.REQUIRE_APPROVAL


def test_after_hours_tightener_outside_window() -> None:
    """Daytime purchase: no tightening (inspector abstains)."""
    from datetime import UTC, datetime
    from unittest.mock import patch

    from capabledeputy.substrate.decision_inspectors_builtin import (
        AfterHoursPurchaseTightener,
    )

    inspector = AfterHoursPurchaseTightener(start_hour_utc=22, end_hour_utc=6)
    action = _FakeAction(kind=_FakeKind("QUEUE_PURCHASE"), target="amazon.com")

    frozen_time = datetime(2026, 5, 21, 14, 0, 0, tzinfo=UTC)  # 2 PM UTC
    with patch("capabledeputy.substrate.decision_inspectors_builtin.datetime") as mock_dt:
        mock_dt.now.return_value = frozen_time
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        result = inspector.inspect(
            action=action,
            session=None,
            proposed_outcome=_FakeDecision(decision=Decision.ALLOW),
        )
    assert result is None


def test_after_hours_tightener_skips_non_purchase() -> None:
    from capabledeputy.substrate.decision_inspectors_builtin import (
        AfterHoursPurchaseTightener,
    )

    inspector = AfterHoursPurchaseTightener()
    action = _FakeAction(kind=_FakeKind("READ_FS"), target="x.txt")
    result = inspector.inspect(
        action=action,
        session=None,
        proposed_outcome=_FakeDecision(decision=Decision.ALLOW),
    )
    assert result is None
