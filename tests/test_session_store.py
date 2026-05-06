from pathlib import Path
from uuid import uuid4

import pytest

from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import Label
from capabledeputy.session.graph import SessionGraph
from capabledeputy.session.model import Session, SessionStatus, Turn
from capabledeputy.session.store import SchemaVersionError, SessionStore


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
    store = SessionStore(store_path)
    s = Session.new(
        label_set=frozenset({Label.CONFIDENTIAL_HEALTH, Label.CONFIDENTIAL_PERSONAL}),
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


async def test_schema_version_mismatch_raises(store_path: Path) -> None:
    s1 = SessionStore(store_path)
    await s1.initialize()

    import sqlite3

    conn = sqlite3.connect(str(store_path))
    conn.execute("UPDATE schema_version SET version = 999")
    conn.commit()
    conn.close()

    s2 = SessionStore(store_path)
    with pytest.raises(SchemaVersionError):
        await s2.initialize()


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
