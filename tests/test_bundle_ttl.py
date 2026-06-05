"""Roadmap v2 #6 — stale-bundle TTL (cookbook §6 T14).

Tests cover:
  - default 24h TTL applied at bundle construction
  - CAPDEP_BUNDLE_TTL_SECONDS overrides the default
  - CAPDEP_BUNDLE_TTL_SECONDS=0 ⇒ immortal bundle
  - approve_all / deny_all preserve expires_at
  - to_dict / _impact_from_dict round-trip expires_at
  - is_expired honors the deadline with an injectable clock
  - BundleExpiredError carries the bundle_id + deadline
  - daemon's bundle_execute refuses an expired bundle
    with error_code='bundle_expired'
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from capabledeputy.approval.bundle import (
    BundledApproval,
    BundleExpiredError,
    GateState,
    WorkflowImpact,
    WorkflowStep,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def clear_ttl_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts with the env scrubbed so the default
    surfaces unless the test sets it explicitly."""
    monkeypatch.delenv("CAPDEP_BUNDLE_TTL_SECONDS", raising=False)


# --- Construction-time TTL ----------------------------------------------


def test_default_ttl_is_24_hours() -> None:
    impact = WorkflowImpact()
    assert impact.expires_at is not None
    delta = impact.expires_at - impact.created_at
    assert delta == timedelta(hours=24)


def test_env_overrides_default_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAPDEP_BUNDLE_TTL_SECONDS", "60")
    impact = WorkflowImpact()
    assert impact.expires_at is not None
    delta = impact.expires_at - impact.created_at
    assert delta == timedelta(seconds=60)


def test_env_zero_means_immortal(monkeypatch: pytest.MonkeyPatch) -> None:
    """TTL=0 is the operator's explicit opt-out — the bundle
    never expires. Validates the env path produces None on
    expires_at, not a degenerate value like the creation time."""
    monkeypatch.setenv("CAPDEP_BUNDLE_TTL_SECONDS", "0")
    impact = WorkflowImpact()
    assert impact.expires_at is None
    assert not impact.is_expired()


def test_env_malformed_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """An operator that fat-fingered the env var into 'forever'
    shouldn't silently lose the safety net. Fall back to the
    default rather than raising at every bundle creation."""
    monkeypatch.setenv("CAPDEP_BUNDLE_TTL_SECONDS", "forever")
    impact = WorkflowImpact()
    assert impact.expires_at is not None
    assert impact.expires_at - impact.created_at == timedelta(hours=24)


def test_explicit_expires_at_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """When a caller supplies expires_at directly (e.g. when
    rehydrating a persisted bundle), the env doesn't overwrite
    it. Re-running dry_run is the only path to a fresh TTL."""
    monkeypatch.setenv("CAPDEP_BUNDLE_TTL_SECONDS", "60")
    pinned = datetime(2030, 1, 1, tzinfo=UTC)
    impact = WorkflowImpact(expires_at=pinned)
    assert impact.expires_at == pinned


# --- is_expired() and BundleExpiredError --------------------------------


def test_is_expired_with_injected_clock() -> None:
    impact = WorkflowImpact()
    assert impact.expires_at is not None
    # Before deadline
    assert not impact.is_expired(now=impact.created_at)
    # At deadline → expired (>=)
    assert impact.is_expired(now=impact.expires_at)
    # After deadline
    assert impact.is_expired(now=impact.expires_at + timedelta(minutes=1))


def test_immortal_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAPDEP_BUNDLE_TTL_SECONDS", "0")
    impact = WorkflowImpact()
    far_future = datetime(2099, 12, 31, tzinfo=UTC)
    assert not impact.is_expired(now=far_future)


def test_bundle_expired_error_carries_context() -> None:
    deadline = datetime(2026, 1, 1, tzinfo=UTC)
    bundle_id = UUID("11111111-1111-1111-1111-111111111111")
    err = BundleExpiredError(bundle_id, deadline)
    assert err.bundle_id == bundle_id
    assert err.expires_at == deadline
    msg = str(err)
    assert "expired" in msg
    assert "re-run dry_run" in msg
    assert "11111111" in msg


# --- Round-trip serialization ------------------------------------------


def test_to_dict_includes_expires_at() -> None:
    impact = WorkflowImpact()
    snapshot = impact.to_dict()
    assert "expires_at" in snapshot
    assert snapshot["expires_at"] is not None


def test_to_dict_expires_at_none_for_immortal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAPDEP_BUNDLE_TTL_SECONDS", "0")
    impact = WorkflowImpact()
    assert impact.to_dict()["expires_at"] is None


def test_round_trip_via_daemon_handler() -> None:
    """The daemon's _impact_from_dict (the inverse of to_dict)
    must reconstruct expires_at. Verifies the round-trip a
    real RPC flow makes."""
    from capabledeputy.daemon.bundle_handlers import _impact_from_dict

    original = WorkflowImpact(
        program_hash="abcd",
        steps=[
            WorkflowStep(
                step_index=0,
                tool_name="t",
                args={},
                arg_labels=frozenset(),
                decision="allow",
                inherent_labels=frozenset(),
                rule=None,
                reason=None,
                line=None,
            ),
        ],
        gates=[
            BundledApproval(
                step_index=0,
                tool_name="t",
                args={},
                arg_labels=frozenset(),
                rule=None,
                reason=None,
                state=GateState.PENDING,
            ),
        ],
    )
    snapshot = original.to_dict()
    rehydrated = _impact_from_dict(snapshot)
    assert rehydrated.bundle_id == original.bundle_id
    assert rehydrated.expires_at == original.expires_at
    assert rehydrated.created_at == original.created_at


def test_legacy_dict_without_expires_at_gets_default_ttl() -> None:
    """A snapshot from a pre-v2 daemon has no expires_at field
    — `_impact_from_dict` returns a bundle with the standard
    24h TTL applied. The operator gets safety by default; they
    can opt out via env if they really meant immortal."""
    from capabledeputy.daemon.bundle_handlers import _impact_from_dict

    legacy = {
        "bundle_id": "22222222-2222-2222-2222-222222222222",
        "program_hash": "x",
        "created_at": "2026-01-01T00:00:00+00:00",
        "steps": [],
        "gates": [],
    }
    rehydrated = _impact_from_dict(legacy)
    assert rehydrated.expires_at is not None
    assert rehydrated.expires_at - rehydrated.created_at == timedelta(hours=24)


# --- approve_all / deny_all preserve expires_at -------------------------


def test_approve_all_preserves_expires_at() -> None:
    impact = WorkflowImpact(
        gates=[
            BundledApproval(
                step_index=0,
                tool_name="t",
                args={},
                arg_labels=frozenset(),
                rule=None,
                reason=None,
                state=GateState.PENDING,
            ),
        ],
    )
    approved = impact.approve_all()
    assert approved.expires_at == impact.expires_at
    assert approved.created_at == impact.created_at


def test_deny_all_preserves_expires_at() -> None:
    impact = WorkflowImpact(
        gates=[
            BundledApproval(
                step_index=0,
                tool_name="t",
                args={},
                arg_labels=frozenset(),
                rule=None,
                reason=None,
                state=GateState.PENDING,
            ),
        ],
    )
    denied = impact.deny_all()
    assert denied.expires_at == impact.expires_at


# --- daemon bundle_execute refuses an expired bundle ------------------


@pytest.mark.anyio
async def test_bundle_execute_refuses_expired_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A handler test: rather than spin a daemon, we exercise
    the handler factory's bundle_execute directly with a
    pre-expired bundle and confirm the structured refusal.
    Validates the operator path that catches T14 in the
    field."""
    from capabledeputy.daemon.bundle_handlers import make_bundle_handlers

    # Forge an App double that exposes only the handler needs.
    class _AppStub:
        registry = None
        tool_client = None
        graph = None
        audit = None

    handlers = make_bundle_handlers(_AppStub())
    bundle_execute = handlers["programmatic.bundle_execute"]

    # Construct an expired-at-birth bundle: created two days
    # ago, default 24h TTL. Use explicit expires_at to skip the
    # post-init refresh.
    past = datetime(2026, 1, 1, tzinfo=UTC)
    expired_impact = WorkflowImpact(
        created_at=past,
        expires_at=past + timedelta(hours=24),
    )
    assert expired_impact.is_expired()

    result = await bundle_execute(
        {
            "source": "pass",
            "session_id": "00000000-0000-0000-0000-000000000000",
            "impact": expired_impact.to_dict(),
        },
    )
    assert result["ok"] is False
    assert result["error_code"] == "bundle_expired"
    assert "expired" in result["error"]
    assert result["bundle_id"] == str(expired_impact.bundle_id)
