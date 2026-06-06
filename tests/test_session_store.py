from pathlib import Path
from uuid import uuid4

import pytest

from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import CategoryTag
from capabledeputy.policy.tiers import Tier
from capabledeputy.session.graph import SessionGraph
from capabledeputy.session.model import Session, SessionStatus, Turn
from capabledeputy.session.store import SCHEMA_VERSION, SessionStore


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "state.db"


async def test_initialize_creates_schema(store_path: Path) -> None:
    store = SessionStore(store_path)
    await store.initialize()
    assert store_path.exists()


async def test_upsert_and_get_round_trip(store_path: Path) -> None:
    store = SessionStore(store_path)
    s = Session.new(owner="marc", intent="test")
    await store.upsert(s)
    loaded = await store.get(s.id)
    assert loaded == s


async def test_upsert_replaces_existing(store_path: Path) -> None:
    store = SessionStore(store_path)
    s = Session.new(intent="initial")
    await store.upsert(s)

    updated = s.with_status(SessionStatus.PAUSED)
    await store.upsert(updated)

    loaded = await store.get(s.id)
    assert loaded is not None
    assert loaded.status == SessionStatus.PAUSED


async def test_get_unknown_returns_none(store_path: Path) -> None:
    store = SessionStore(store_path)
    result = await store.get(uuid4())
    assert result is None


async def test_all_returns_in_creation_order(store_path: Path) -> None:
    store = SessionStore(store_path)
    a = Session.new(intent="first")
    await store.upsert(a)
    b = Session.new(intent="second")
    await store.upsert(b)
    c = Session.new(intent="third")
    await store.upsert(c)

    results = await store.all()
    assert [s.intent for s in results] == ["first", "second", "third"]


async def test_history_and_label_set_persist(store_path: Path) -> None:
    from capabledeputy.policy.labels import AxisA

    store = SessionStore(store_path)
    s = Session.new(
        axis_a=AxisA(
            categories=(
                CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared"),
                CategoryTag("personal", Tier.REGULATED, assignment_provenance="source-declared"),
            )
        ),
        capability_set=frozenset(
            {Capability(kind=CapabilityKind.READ_FS, pattern="/health/*")},
        ),
        history=(Turn(turn_id=0, role="user", content="hi"),),
    )
    await store.upsert(s)
    loaded = await store.get(s.id)
    assert loaded == s


async def test_initialize_is_idempotent(store_path: Path) -> None:
    s1 = SessionStore(store_path)
    await s1.initialize()
    await s1.initialize()
    s2 = SessionStore(store_path)
    await s2.initialize()


async def test_schema_version_mismatch_wipes(store_path: Path) -> None:
    """§R6 no-backwards-compat: a db at a foreign schema version is
    WIPED and recreated clean, not migrated and not raised on.
    Pre-existing sessions are gone; the version is reset to current."""
    import sqlite3

    s1 = SessionStore(store_path)
    await s1.initialize()
    sess = Session.new(owner="o")
    await s1.upsert(sess)
    assert await s1.get(sess.id) is not None

    # Stamp a foreign version (simulating an older on-disk schema).
    conn = sqlite3.connect(str(store_path))
    conn.execute("UPDATE schema_version SET version = 999")
    conn.commit()
    conn.close()

    s2 = SessionStore(store_path)
    await s2.initialize()  # wipes — does not raise
    assert await s2.get(sess.id) is None  # old data gone

    conn = sqlite3.connect(str(store_path))
    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    conn.close()
    assert version == SCHEMA_VERSION


async def test_graph_persists_sessions_through_store(store_path: Path) -> None:
    store = SessionStore(store_path)
    g1 = SessionGraph(store=store)
    parent = await g1.new(intent="root")
    child = await g1.fork(parent.id, intent="branch")
    await g1.pause(child.id)

    g2 = SessionGraph(store=store)
    await g2.load()
    assert parent.id in g2
    assert child.id in g2
    assert g2.get(child.id).status == SessionStatus.PAUSED
    assert g2.get(child.id).parent == parent.id


async def test_load_without_store_is_noop(store_path: Path) -> None:
    g = SessionGraph()
    await g.load()
    assert len(g) == 0


async def test_persisted_sessions_survive_full_state_change(store_path: Path) -> None:
    store = SessionStore(store_path)
    g1 = SessionGraph(store=store)
    s = await g1.new()
    await g1.pause(s.id)
    await g1.resume(s.id)
    await g1.abort(s.id)

    g2 = SessionGraph(store=store)
    await g2.load()
    assert g2.get(s.id).status == SessionStatus.ABORTED


# --- Time-bounded capabilities (feature 001, T009 / FR-008 / SC-004) -----


async def test_time_bounded_capability_survives_store_reload(
    store_path: Path,
) -> None:
    """A persisted absolute deadline is unchanged after a simulated
    runtime restart (a fresh SessionStore over the same DB), and a
    post-deadline decision still denies with capability-expired."""
    from datetime import UTC, datetime, timedelta

    from capabledeputy.policy.actions import Action
    from capabledeputy.policy.engine import (
        CAPABILITY_EXPIRED_RULE,
        decide,
    )
    from capabledeputy.policy.rules import Decision

    deadline = datetime(2026, 3, 1, 9, 0, 0, tzinfo=UTC)
    cap = Capability(
        kind=CapabilityKind.READ_FS,
        pattern="*",
        expires_at=deadline,
    )
    s = Session.new(intent="ttl", capability_set=frozenset({cap}))

    store = SessionStore(store_path)
    await store.upsert(s)

    # Simulate restart: brand-new store instance over the same file.
    reloaded_store = SessionStore(store_path)
    loaded = await reloaded_store.get(s.id)
    assert loaded is not None
    [loaded_cap] = list(loaded.capability_set)
    assert loaded_cap.expires_at == deadline  # absolute, unchanged

    # Past the original deadline → still denies, attributed to expiry.
    result = decide(
        loaded.capability_set,
        Action(kind=CapabilityKind.READ_FS, target="/x"),
        now=deadline + timedelta(hours=1),
    )
    assert result.decision == Decision.DENY
    assert result.rule == CAPABILITY_EXPIRED_RULE


async def test_revoked_audit_ids_survives_reload(store_path: Path) -> None:
    """002 T007/T009: the session-level revoked-set is additive,
    default-tolerant, and round-trips through the store."""
    from dataclasses import replace

    store = SessionStore(store_path)
    s = Session.new(intent="deleg")
    aid = uuid4()
    s = replace(s, revoked_audit_ids=frozenset({aid}))
    await store.upsert(s)
    loaded = await store.get(s.id)
    assert loaded is not None
    assert loaded.revoked_audit_ids == frozenset({aid})
    assert loaded == s


async def test_legacy_session_dict_without_revoked_set(store_path: Path) -> None:
    s = Session.new(intent="legacy")
    d = s.to_dict()
    del d["revoked_audit_ids"]
    assert Session.from_dict(d).revoked_audit_ids == frozenset()
