"""Tests for Axis D (DecisionContext) — first-class decision context type (T136, FR-006).

Verifies:
- DecisionContext dataclass is frozen (no post-construction mutation)
- to_dict/from_dict round-trip preserves values
- from_dict({}) and from_dict(None) return fail-closed defaults
- All AuthLevel values map sensibly
- expectedness defaults to "anomalous" (fail-closed per FR-029)
- reversibility_degree/agent default to "irreversible"/"external" (fail-closed per FR-037)
- fail_closed() returns the most-restrictive context
"""

from __future__ import annotations

import pytest

from capabledeputy.policy.axis_d import AuthLevel, DecisionContext


class TestDecisionContextFrozen:
    """DecisionContext is @dataclass(frozen=True): no post-construction mutation."""

    def test_frozen_raises_on_mutation(self) -> None:
        ctx = DecisionContext()
        with pytest.raises(AttributeError):
            ctx.initiator = "changed"  # type: ignore


class TestAuthLevel:
    """AuthLevel enum covers authentication strength values."""

    def test_all_auth_levels_present(self) -> None:
        levels = {level.value for level in AuthLevel}
        expected = {
            "unauthenticated",
            "system-internal",
            "principal-direct",
            "operator-confirmed",
        }
        assert levels == expected

    def test_auth_level_string_value(self) -> None:
        assert str(AuthLevel.UNAUTHENTICATED) == "unauthenticated"
        assert str(AuthLevel.SYSTEM_INTERNAL) == "system-internal"
        assert str(AuthLevel.PRINCIPAL_DIRECT) == "principal-direct"
        assert str(AuthLevel.OPERATOR_CONFIRMED) == "operator-confirmed"


class TestDecisionContextDefaults:
    """Default values are fail-closed per Principle VI."""

    def test_default_initiator_is_unset(self) -> None:
        ctx = DecisionContext()
        assert ctx.initiator == "unset"

    def test_default_initiator_authentication_is_unauthenticated(self) -> None:
        ctx = DecisionContext()
        assert ctx.initiator_authentication == AuthLevel.UNAUTHENTICATED

    def test_default_counterparty_is_none(self) -> None:
        ctx = DecisionContext()
        assert ctx.counterparty is None

    def test_default_relationship_group_ids_is_empty_frozenset(self) -> None:
        ctx = DecisionContext()
        assert ctx.relationship_group_ids == frozenset()

    def test_default_expectedness_is_anomalous(self) -> None:
        ctx = DecisionContext()
        assert ctx.expectedness == "anomalous"

    def test_default_reversibility_degree_is_irreversible(self) -> None:
        ctx = DecisionContext()
        assert ctx.reversibility_degree == "irreversible"

    def test_default_reversibility_agent_is_external(self) -> None:
        ctx = DecisionContext()
        assert ctx.reversibility_agent == "external"


class TestDecisionContextToDict:
    """to_dict() serialization for Session.to_dict() JSON column."""

    def test_to_dict_with_defaults(self) -> None:
        ctx = DecisionContext()
        d = ctx.to_dict()
        assert d == {
            "initiator": "unset",
            "initiator_authentication": "unauthenticated",
            "counterparty": None,
            "relationship_group_ids": [],
            "expectedness": "anomalous",
            "reversibility_degree": "irreversible",
            "reversibility_agent": "external",
        }

    def test_to_dict_with_values(self) -> None:
        ctx = DecisionContext(
            initiator="user@example.com",
            initiator_authentication=AuthLevel.OPERATOR_CONFIRMED,
            counterparty="slack.com",
            relationship_group_ids=frozenset({"project-alpha", "tier-1"}),
            expectedness="expected",
            reversibility_degree="reversible",
            reversibility_agent="system",
        )
        d = ctx.to_dict()
        assert d["initiator"] == "user@example.com"
        assert d["initiator_authentication"] == "operator-confirmed"
        assert d["counterparty"] == "slack.com"
        assert sorted(d["relationship_group_ids"]) == ["project-alpha", "tier-1"]
        assert d["expectedness"] == "expected"
        assert d["reversibility_degree"] == "reversible"
        assert d["reversibility_agent"] == "system"

    def test_to_dict_relationship_group_ids_sorted(self) -> None:
        """Serialization sorts relationship group IDs for deterministic output."""
        ctx = DecisionContext(
            relationship_group_ids=frozenset({"z", "a", "m"}),
        )
        d = ctx.to_dict()
        assert d["relationship_group_ids"] == ["a", "m", "z"]


class TestDecisionContextFromDict:
    """from_dict() deserialization with default tolerance."""

    def test_from_dict_none_returns_fail_closed(self) -> None:
        ctx = DecisionContext.from_dict(None)
        assert ctx.initiator == "unset"
        assert ctx.initiator_authentication == AuthLevel.UNAUTHENTICATED
        assert ctx.counterparty is None
        assert ctx.expectedness == "anomalous"
        assert ctx.reversibility_degree == "irreversible"
        assert ctx.reversibility_agent == "external"

    def test_from_dict_empty_dict_returns_fail_closed(self) -> None:
        ctx = DecisionContext.from_dict({})
        assert ctx.initiator == "unset"
        assert ctx.initiator_authentication == AuthLevel.UNAUTHENTICATED
        assert ctx.counterparty is None
        assert ctx.expectedness == "anomalous"

    def test_from_dict_with_values(self) -> None:
        d = {
            "initiator": "user@example.com",
            "initiator_authentication": "operator-confirmed",
            "counterparty": "slack.com",
            "relationship_group_ids": ["project-alpha", "tier-1"],
            "expectedness": "expected",
            "reversibility_degree": "reversible",
            "reversibility_agent": "system",
        }
        ctx = DecisionContext.from_dict(d)
        assert ctx.initiator == "user@example.com"
        assert ctx.initiator_authentication == AuthLevel.OPERATOR_CONFIRMED
        assert ctx.counterparty == "slack.com"
        assert ctx.relationship_group_ids == frozenset({"project-alpha", "tier-1"})
        assert ctx.expectedness == "expected"
        assert ctx.reversibility_degree == "reversible"
        assert ctx.reversibility_agent == "system"

    def test_from_dict_invalid_auth_level_defaults_to_unauthenticated(self) -> None:
        d = {"initiator_authentication": "invalid_value"}
        ctx = DecisionContext.from_dict(d)
        assert ctx.initiator_authentication == AuthLevel.UNAUTHENTICATED

    def test_from_dict_invalid_expectedness_defaults_to_anomalous(self) -> None:
        d = {"expectedness": "invalid_value"}
        ctx = DecisionContext.from_dict(d)
        assert ctx.expectedness == "anomalous"

    def test_from_dict_invalid_reversibility_degree_defaults_to_irreversible(self) -> None:
        d = {"reversibility_degree": "invalid_value"}
        ctx = DecisionContext.from_dict(d)
        assert ctx.reversibility_degree == "irreversible"

    def test_from_dict_invalid_reversibility_agent_defaults_to_external(self) -> None:
        d = {"reversibility_agent": "invalid_value"}
        ctx = DecisionContext.from_dict(d)
        assert ctx.reversibility_agent == "external"

    def test_from_dict_partial_dict(self) -> None:
        """Partial dict with some fields missing uses defaults."""
        d = {
            "initiator": "user@example.com",
            "counterparty": "slack.com",
            # initiator_authentication, expectedness, reversibility_* missing
        }
        ctx = DecisionContext.from_dict(d)
        assert ctx.initiator == "user@example.com"
        assert ctx.counterparty == "slack.com"
        assert ctx.initiator_authentication == AuthLevel.UNAUTHENTICATED
        assert ctx.expectedness == "anomalous"
        assert ctx.reversibility_degree == "irreversible"

    def test_from_dict_coerces_relationship_group_ids_to_frozenset(self) -> None:
        d = {"relationship_group_ids": ["g1", "g2", "g1"]}  # with duplicate
        ctx = DecisionContext.from_dict(d)
        assert ctx.relationship_group_ids == frozenset({"g1", "g2"})


class TestDecisionContextRoundTrip:
    """to_dict/from_dict round-trip is idempotent."""

    def test_roundtrip_with_defaults(self) -> None:
        ctx1 = DecisionContext()
        d = ctx1.to_dict()
        ctx2 = DecisionContext.from_dict(d)
        assert ctx1 == ctx2

    def test_roundtrip_with_values(self) -> None:
        ctx1 = DecisionContext(
            initiator="alice@example.com",
            initiator_authentication=AuthLevel.PRINCIPAL_DIRECT,
            counterparty="github.com",
            relationship_group_ids=frozenset({"eng", "tier-1-vendors"}),
            expectedness="expected",
            reversibility_degree="costly-reversible",
            reversibility_agent="human",
        )
        d = ctx1.to_dict()
        ctx2 = DecisionContext.from_dict(d)
        assert ctx1 == ctx2
        assert ctx2.to_dict() == d

    def test_roundtrip_with_none_counterparty(self) -> None:
        ctx1 = DecisionContext(counterparty=None)
        d = ctx1.to_dict()
        ctx2 = DecisionContext.from_dict(d)
        assert ctx1 == ctx2
        assert ctx2.counterparty is None

    def test_roundtrip_with_empty_relationship_groups(self) -> None:
        ctx1 = DecisionContext(relationship_group_ids=frozenset())
        d = ctx1.to_dict()
        ctx2 = DecisionContext.from_dict(d)
        assert ctx1 == ctx2
        assert ctx2.relationship_group_ids == frozenset()


class TestDecisionContextFailClosed:
    """fail_closed() returns the most-restrictive default context."""

    def test_fail_closed_returns_default_context(self) -> None:
        ctx = DecisionContext.fail_closed()
        assert ctx == DecisionContext()

    def test_fail_closed_is_most_restrictive(self) -> None:
        ctx = DecisionContext.fail_closed()
        # All restrictive defaults:
        assert ctx.initiator == "unset"
        assert ctx.initiator_authentication == AuthLevel.UNAUTHENTICATED
        assert ctx.counterparty is None
        assert ctx.relationship_group_ids == frozenset()
        assert ctx.expectedness == "anomalous"
        assert ctx.reversibility_degree == "irreversible"
        assert ctx.reversibility_agent == "external"


class TestDecisionContextEquality:
    """Equality and hashing behavior."""

    def test_equal_contexts_are_equal(self) -> None:
        ctx1 = DecisionContext(initiator="user@example.com")
        ctx2 = DecisionContext(initiator="user@example.com")
        assert ctx1 == ctx2

    def test_different_initiator_makes_unequal(self) -> None:
        ctx1 = DecisionContext(initiator="user1@example.com")
        ctx2 = DecisionContext(initiator="user2@example.com")
        assert ctx1 != ctx2

    def test_can_be_hashed_in_set(self) -> None:
        """Frozen dataclass is hashable."""
        ctx1 = DecisionContext(initiator="user@example.com")
        ctx2 = DecisionContext(initiator="user@example.com")
        s = {ctx1, ctx2}
        assert len(s) == 1  # Both equal, so set deduplicates

    def test_dict_keys_preserves_set_dedup(self) -> None:
        """Frozen dataclass can be a dict key."""
        ctx1 = DecisionContext(initiator="alice@example.com")
        ctx2 = DecisionContext(initiator="alice@example.com")
        d = {ctx1: "value1", ctx2: "value2"}
        assert len(d) == 1
        assert d[ctx1] == "value2"


class TestDecisionContextBackwardCompat:
    """Backward compatibility with old field names and formats."""

    def test_from_dict_old_authentication_field_name(self) -> None:
        """Old code used 'authentication' field; map it to new initiator_authentication."""
        d = {
            "initiator": "user@example.com",
            "authentication": "operator-confirmed",  # Old field name
        }
        ctx = DecisionContext.from_dict(d)
        assert ctx.initiator_authentication == AuthLevel.OPERATOR_CONFIRMED

    def test_from_dict_old_reversibility_dict_format(self) -> None:
        """Old code stored reversibility as a dict with degree/agent keys."""
        d = {
            "initiator": "user@example.com",
            "reversibility": {
                "degree": "reversible",
                "agent": "system",
            },
        }
        ctx = DecisionContext.from_dict(d)
        assert ctx.reversibility_degree == "reversible"
        assert ctx.reversibility_agent == "system"

    def test_reversibility_property_returns_dict(self) -> None:
        """The reversibility property provides dict-like access for old code."""
        ctx = DecisionContext(
            reversibility_degree="reversible",
            reversibility_agent="human",
        )
        rev_dict = ctx.reversibility
        assert rev_dict["degree"] == "reversible"
        assert rev_dict["agent"] == "human"
        assert rev_dict.get("degree") == "reversible"

    def test_authentication_property_returns_string(self) -> None:
        """The authentication property provides string access for old code."""
        ctx = DecisionContext(
            initiator_authentication=AuthLevel.PRINCIPAL_DIRECT,
        )
        assert ctx.authentication == "principal-direct"
        assert isinstance(ctx.authentication, str)

    def test_old_test_data_with_device_bound_auth(self) -> None:
        """Test data from T039 tests uses 'device-bound' auth string."""
        d = {
            "initiator": "cron:backup-job",
            "authentication": "device-bound",
            "expectedness": "expected",
            "reversibility": {"degree": "reversible", "agent": "system"},
        }
        # Should not raise, defaults to UNAUTHENTICATED if value is unrecognized
        ctx = DecisionContext.from_dict(d)
        assert ctx.initiator == "cron:backup-job"
        assert ctx.expectedness == "expected"


class TestAxisDBackwardCompatAlias:
    """AxisD is an alias for DecisionContext (backward compat with Session)."""

    def test_axis_d_is_decision_context(self) -> None:
        from capabledeputy.policy.labels import AxisD

        assert AxisD is DecisionContext

    def test_axis_d_imported_in_session(self) -> None:
        """Session imports AxisD from labels and uses it correctly."""
        from capabledeputy.session.model import Session
        from uuid import uuid4

        # Create a session with axis_d
        session = Session.new(
            owner="test_user",
            axis_d=DecisionContext(
                initiator="test_user",
                counterparty="example.com",
            ),
        )
        assert session.axis_d.initiator == "test_user"
        assert session.axis_d.counterparty == "example.com"

    def test_axis_d_serialization_roundtrip_in_session(self) -> None:
        """Session.to_dict/from_dict preserves axis_d."""
        from capabledeputy.session.model import Session

        session1 = Session.new(
            owner="test_user",
            axis_d=DecisionContext(
                initiator="alice@example.com",
                initiator_authentication=AuthLevel.OPERATOR_CONFIRMED,
                counterparty="slack.com",
                expectedness="expected",
                reversibility_degree="reversible",
                reversibility_agent="system",
            ),
        )
        d = session1.to_dict()
        session2 = Session.from_dict(d)
        assert session2.axis_d == session1.axis_d
        assert session2.axis_d.initiator == "alice@example.com"
        assert session2.axis_d.counterparty == "slack.com"
