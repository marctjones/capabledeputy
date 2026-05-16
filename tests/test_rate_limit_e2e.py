"""End-to-end: rate-limited capabilities through the policy chokepoint.

The runtime records each ALLOW dispatch against the matched
capability's sliding-window log; the (N+1)th within the window is
denied with `rate-limit-exceeded`. Deterministic: no real LLM.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest

from capabledeputy.app import App
from capabledeputy.policy.capabilities import Capability, CapabilityKind, RateLimit


@pytest.fixture
async def app(tmp_path: Path) -> App:
    a = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await a.startup()
    return a


async def test_n_allowed_then_n_plus_one_denied(app: App) -> None:
    cap = Capability(
        kind=CapabilityKind.READ_FS,
        pattern="*",
        rate_limit=RateLimit(max_uses=3, window_seconds=3600),
    )
    s = await app.graph.new()
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))

    # First 3 dispatches allowed; each records a use.
    for _ in range(3):
        o = await app.tool_client.call_tool(s.id, "memory.read", {"key": "k"})
        assert o.decision.value == "allow"

    # 4th within the window → denied, attributed to the rate limit.
    o4 = await app.tool_client.call_tool(s.id, "memory.read", {"key": "k"})
    assert o4.decision.value == "deny"
    assert o4.rule == "rate-limit-exceeded"
    assert "rate limit exceeded" in (o4.reason or "")

    # The use log is keyed by the capability's audit_id and capped at
    # max_uses within the window (pruned on record).
    after = app.graph.get(s.id)
    stamps = after.cap_uses[str(cap.audit_id)]
    assert len(stamps) == 3


async def test_rate_limit_survives_store_reload(tmp_path: Path) -> None:
    """SC-style: the use log persists across a simulated restart, so
    the limit is not silently reset by bouncing the daemon."""
    from capabledeputy.session.model import Session
    from capabledeputy.session.store import SessionStore

    aid = UUID("22222222-2222-2222-2222-222222222222")
    cap = Capability(
        kind=CapabilityKind.READ_FS, pattern="*", audit_id=aid,
        rate_limit=RateLimit(max_uses=1, window_seconds=3600),
    )
    from datetime import UTC, datetime

    s = Session.new(intent="rl", capability_set=frozenset({cap}))
    s = replace(
        s, cap_uses={str(aid): (datetime.now(UTC),)},
    )
    store = SessionStore(tmp_path / "s.db")
    await store.upsert(s)

    reloaded = await SessionStore(tmp_path / "s.db").get(s.id)
    assert reloaded is not None
    assert str(aid) in reloaded.cap_uses
    assert len(reloaded.cap_uses[str(aid)]) == 1


async def test_invariant_identical_with_preview_disabled(tmp_path: Path) -> None:
    """SC-006-style: rate-limit enforcement is byte-identical whether
    or not policy.preview exists; no LLM on the path."""
    cap = Capability(
        kind=CapabilityKind.READ_FS, pattern="*",
        rate_limit=RateLimit(max_uses=1, window_seconds=3600),
    )
    results = {}
    for preview in (True, False):
        a = App(
            state_db_path=tmp_path / f"s-{preview}.db",
            audit_log_path=tmp_path / f"a-{preview}.jsonl",
            enable_policy_preview=preview,
        )
        await a.startup()
        assert a.llm_client is None
        s = await a.graph.new()
        a.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))
        await a.tool_client.call_tool(s.id, "memory.read", {"key": "k"})
        o2 = await a.tool_client.call_tool(s.id, "memory.read", {"key": "k"})
        results[preview] = (o2.decision.value, o2.rule)
    assert results[True] == results[False] == ("deny", "rate-limit-exceeded")
