import sqlite3
from contextlib import closing

import pytest

from capabledeputy.agent.loop import run_turn
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.daemon.session_handlers import make_session_handlers
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.mode.dispatcher import ModeSelectionError
from capabledeputy.session.graph import SessionGraph
from capabledeputy.session.model import Session
from capabledeputy.session.store import SessionStore
from capabledeputy.tools.client import LabeledToolClient
from capabledeputy.tools.registry import ToolRegistry


async def test_admission_restart_and_fork_preserve_requirement(tmp_path):
    path = tmp_path / "state.db"
    graph = SessionGraph(store=SessionStore(path))
    handlers = make_session_handlers(graph)
    data = await handlers["session.new"]({"required_modes": ["reference", "sealed"]})
    assert data["required_modes"] == ["reference", "sealed"]
    s = graph.list()[0]
    fork = await graph.fork(s.id)
    assert fork.required_modes == s.required_modes
    restarted = SessionGraph(store=SessionStore(path))
    await restarted.load()
    assert restarted.get(s.id).required_modes == s.required_modes
    assert restarted.get(fork.id).required_modes == s.required_modes
    with pytest.raises(ValueError):
        await handlers["session.new"]({"required_modes": ["unknown"]})
    assert len(graph) == 2


async def test_empty_requirement_refuses_before_model_or_history(tmp_path):
    graph = SessionGraph()
    s = await graph.new(required_modes=frozenset())
    writer = AuditWriter(tmp_path / "audit.jsonl")
    registry = ToolRegistry()
    llm = FakeLLMClient([])
    with pytest.raises(ModeSelectionError, match="required handling"):
        await run_turn(
            session_id=s.id,
            user_message="do work",
            graph=graph,
            llm=llm,
            registry=registry,
            tool_client=LabeledToolClient(registry, graph, writer),
            audit=writer,
        )
    assert llm.calls == []
    assert graph.get(s.id).history == ()


async def test_v9_migration_preserves_state_and_request_records(tmp_path):
    path = tmp_path / "state.db"
    store = SessionStore(path)
    s = Session.new(intent="preserve me")
    await store.upsert(s)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS retained_trace (id TEXT PRIMARY KEY, data TEXT)")
        db.execute("INSERT INTO retained_trace VALUES ('r1', 'preserve trace')")
        db.execute("ALTER TABLE sessions DROP COLUMN required_modes")
        db.execute("UPDATE schema_version SET version=9")
    migrated = SessionStore(path)
    assert await migrated.get(s.id) == s
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT data FROM retained_trace").fetchone()[0] == "preserve trace"
    assert migrated.last_recovery_action == "migrated"
    assert migrated.last_backup_path is not None


async def test_corrupt_persisted_requirement_fails_closed(tmp_path):
    path = tmp_path / "state.db"
    store = SessionStore(path)
    s = Session.new(required_modes=frozenset({"sealed"}))
    await store.upsert(s)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE sessions SET required_modes=?", ('["invented"]',))
    with pytest.raises(ValueError):
        await SessionStore(path).get(s.id)
