"""T016 / D1: the `/grant --ttl` CLI behavior is test-gated.

`_handle_grant` ships a capability dict to the daemon via the module
`_call`. We monkeypatch `_call` to capture that dict without a daemon
and assert the `--ttl` translation + error handling.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from capabledeputy.cli import chat


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_call(method: str, params: dict[str, Any] | None = None) -> Any:
        calls.append({"method": method, "params": params or {}})
        return {}

    monkeypatch.setattr(chat, "_call", fake_call)
    return calls


def test_grant_ttl_sets_expires_at_now_plus_ttl(
    captured: list[dict[str, Any]],
) -> None:
    before = datetime.now(UTC)
    chat._handle_grant("QUEUE_PURCHASE amazon --ttl 60", "sess-1")
    after = datetime.now(UTC)

    grant = next(c for c in captured if c["method"] == "session.grant_capability")
    cap = grant["params"]["capability"]
    assert cap["kind"] == "QUEUE_PURCHASE"
    assert cap["pattern"] == "amazon"
    assert cap["expires_at"] is not None
    deadline = datetime.fromisoformat(cap["expires_at"])
    # deadline ≈ now + 60s, within the wall-clock window of the call
    assert before + timedelta(seconds=60) <= deadline <= after + timedelta(seconds=60)


def test_grant_without_ttl_has_no_expiry(
    captured: list[dict[str, Any]],
) -> None:
    chat._handle_grant("READ_FS *", "sess-1")
    grant = next(c for c in captured if c["method"] == "session.grant_capability")
    assert grant["params"]["capability"]["expires_at"] is None


def test_grant_ttl_non_numeric_rejected_no_capability_granted(
    captured: list[dict[str, Any]],
) -> None:
    chat._handle_grant("READ_FS * --ttl abc", "sess-1")
    # Bad --ttl ⇒ early return, nothing sent to the daemon.
    assert not any(
        c["method"] == "session.grant_capability" for c in captured
    )


def test_grant_ttl_zero_is_immediately_expired_when_decided(
    captured: list[dict[str, Any]],
) -> None:
    """ttl 0 produces a deadline == now; the half-open rule makes the
    capability already expired at first use (verified via the engine)."""
    from capabledeputy.policy.actions import Action
    from capabledeputy.policy.capabilities import Capability
    from capabledeputy.policy.engine import CAPABILITY_EXPIRED_RULE, decide
    from capabledeputy.policy.rules import Decision

    chat._handle_grant("READ_FS * --ttl 0", "sess-1")
    grant = next(c for c in captured if c["method"] == "session.grant_capability")
    cap = Capability.from_dict(grant["params"]["capability"])
    assert cap.expires_at is not None
    r = decide(
        frozenset(),
        frozenset({cap}),
        Action(kind=cap.kind, target="/x"),
        now=cap.expires_at,  # exactly the deadline ⇒ expired (half-open)
    )
    assert r.decision == Decision.DENY
    assert r.rule == CAPABILITY_EXPIRED_RULE
