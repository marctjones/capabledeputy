from itertools import pairwise
from pathlib import Path
from uuid import uuid4

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from capabledeputy.audit.events import EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.session.graph import (
    SessionGraph,
    SessionNotFoundError,
    SessionStateError,
)
from capabledeputy.session.model import SessionStatus


@pytest.fixture
def graph() -> SessionGraph:
    return SessionGraph()


@pytest.fixture
def audited_graph(tmp_path: Path) -> tuple[SessionGraph, AuditWriter]:
    writer = AuditWriter(tmp_path / "audit.jsonl")
    return SessionGraph(audit=writer), writer


async def test_new_creates_session(graph: SessionGraph) -> None:
    s = await graph.new(owner="marc", intent="hello")
    assert s.status == SessionStatus.ACTIVE
    assert s.owner == "marc"
    assert s.intent == "hello"
    assert s.id in graph
    assert len(graph) == 1


async def test_fork_inherits_parent_state(graph: SessionGraph) -> None:
    parent = await graph.new()
    child = await graph.fork(parent.id, intent="branch")
    assert child.parent == parent.id
    assert child.id != parent.id
    assert child.status == SessionStatus.ACTIVE
    assert child.label_set == parent.label_set
    assert child.capability_set == parent.capability_set
    assert child.history == parent.history


async def test_fork_does_not_mutate_parent(graph: SessionGraph) -> None:
    parent = await graph.new()
    parent_before = parent
    await graph.fork(parent.id)
    parent_after = graph.get(parent.id)
    assert parent_after == parent_before


async def test_fork_unknown_parent_raises(graph: SessionGraph) -> None:
    with pytest.raises(SessionNotFoundError):
        await graph.fork(uuid4())


async def test_fork_terminal_session_rejected(graph: SessionGraph) -> None:
    s = await graph.new()
    await graph.abort(s.id)
    with pytest.raises(SessionStateError):
        await graph.fork(s.id)


async def test_pause_active_session(graph: SessionGraph) -> None:
    s = await graph.new()
    paused = await graph.pause(s.id)
    assert paused.status == SessionStatus.PAUSED
    assert graph.get(s.id).status == SessionStatus.PAUSED


async def test_pause_already_paused_rejected(graph: SessionGraph) -> None:
    s = await graph.new()
    await graph.pause(s.id)
    with pytest.raises(SessionStateError):
        await graph.pause(s.id)


async def test_resume_paused_session(graph: SessionGraph) -> None:
    s = await graph.new()
    await graph.pause(s.id)
    resumed = await graph.resume(s.id)
    assert resumed.status == SessionStatus.ACTIVE


async def test_resume_active_session_rejected(graph: SessionGraph) -> None:
    s = await graph.new()
    with pytest.raises(SessionStateError):
        await graph.resume(s.id)


async def test_abort_active_session(graph: SessionGraph) -> None:
    s = await graph.new()
    aborted = await graph.abort(s.id)
    assert aborted.status == SessionStatus.ABORTED
    assert aborted.is_terminal


async def test_abort_already_aborted_rejected(graph: SessionGraph) -> None:
    s = await graph.new()
    await graph.abort(s.id)
    with pytest.raises(SessionStateError):
        await graph.abort(s.id)


async def test_list_filters_by_status(graph: SessionGraph) -> None:
    a = await graph.new()
    b = await graph.new()
    await graph.pause(b.id)

    active = graph.list(status=SessionStatus.ACTIVE)
    paused = graph.list(status=SessionStatus.PAUSED)
    assert {s.id for s in active} == {a.id}
    assert {s.id for s in paused} == {b.id}


async def test_list_without_filter_returns_all(graph: SessionGraph) -> None:
    a = await graph.new()
    b = await graph.new()
    all_sessions = graph.list()
    assert {s.id for s in all_sessions} == {a.id, b.id}


async def test_children_returns_forked_sessions(graph: SessionGraph) -> None:
    parent = await graph.new()
    c1 = await graph.fork(parent.id)
    c2 = await graph.fork(parent.id)
    children = graph.children(parent.id)
    assert {s.id for s in children} == {c1.id, c2.id}


async def test_get_unknown_raises(graph: SessionGraph) -> None:
    with pytest.raises(SessionNotFoundError):
        graph.get(uuid4())


async def test_audit_events_emitted_for_lifecycle(
    audited_graph: tuple[SessionGraph, AuditWriter],
) -> None:
    graph, writer = audited_graph
    parent = await graph.new(intent="root")
    child = await graph.fork(parent.id, intent="branch")
    await graph.pause(child.id)
    await graph.resume(child.id)
    await graph.abort(parent.id)

    events = await writer.read_all()
    types = [e.event_type for e in events]
    assert types == [
        EventType.SESSION_CREATED,
        EventType.SESSION_FORKED,
        EventType.SESSION_PAUSED,
        EventType.SESSION_RESUMED,
        EventType.SESSION_ABORTED,
    ]
    assert events[0].session_id == parent.id
    assert events[1].session_id == child.id
    assert events[1].payload["parent_id"] == str(parent.id)


async def test_insert_restores_session(graph: SessionGraph) -> None:
    s1 = await graph.new()
    fresh = SessionGraph()
    fresh.insert(s1)
    assert fresh.get(s1.id) == s1


_INTENT_STRATEGY = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    min_size=0,
    max_size=20,
)


@settings(max_examples=50, deadline=None)
@given(intents=st.lists(_INTENT_STRATEGY, min_size=1, max_size=8))
async def test_property_fork_chain_preserves_parent_pointers(intents: list[str]) -> None:
    graph = SessionGraph()
    root = await graph.new(intent=intents[0])
    chain = [root]
    for intent in intents[1:]:
        child = await graph.fork(chain[-1].id, intent=intent)
        chain.append(child)

    for parent, child in pairwise(chain):
        assert graph.get(child.id).parent == parent.id

    for s in chain:
        assert graph.get(s.id).is_terminal is False


@settings(max_examples=50, deadline=None)
@given(n=st.integers(min_value=1, max_value=20))
async def test_property_n_forks_produce_n_children(n: int) -> None:
    graph = SessionGraph()
    parent = await graph.new()
    children = [await graph.fork(parent.id) for _ in range(n)]

    found = graph.children(parent.id)
    assert {s.id for s in found} == {c.id for c in children}
    assert len(found) == n


@settings(max_examples=50, deadline=None)
@given(
    pause_count=st.integers(min_value=1, max_value=10),
)
async def test_property_pause_resume_cycles(pause_count: int) -> None:
    graph = SessionGraph()
    s = await graph.new()
    for _ in range(pause_count):
        paused = await graph.pause(s.id)
        assert paused.status == SessionStatus.PAUSED
        resumed = await graph.resume(s.id)
        assert resumed.status == SessionStatus.ACTIVE


@settings(max_examples=50, deadline=None)
@given(n=st.integers(min_value=1, max_value=20))
async def test_property_unique_ids_across_n_creates(n: int) -> None:
    graph = SessionGraph()
    sessions = [await graph.new() for _ in range(n)]
    ids = {s.id for s in sessions}
    assert len(ids) == n
