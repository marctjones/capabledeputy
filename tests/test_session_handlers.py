from pathlib import Path
from uuid import UUID, uuid4

import pytest

from capabledeputy.audit.writer import AuditWriter
from capabledeputy.daemon.session_handlers import make_session_handlers
from capabledeputy.session.graph import SessionGraph, SessionNotFoundError, SessionStateError


@pytest.fixture
def graph(tmp_path: Path) -> SessionGraph:
    audit = AuditWriter(tmp_path / "audit.jsonl")
    return SessionGraph(audit=audit)


async def test_session_new_creates_active_session(graph: SessionGraph) -> None:
    handlers = make_session_handlers(graph)
    result = await handlers["session.new"]({"intent": "hello", "owner": "marc"})
    assert result["status"] == "active"
    assert result["intent"] == "hello"
    assert result["owner"] == "marc"
    assert UUID(result["id"]) in graph


async def test_session_list_returns_all_when_no_filter(graph: SessionGraph) -> None:
    handlers = make_session_handlers(graph)
    a = await handlers["session.new"]({})
    b = await handlers["session.new"]({})

    result = await handlers["session.list"]({})
    ids = {s["id"] for s in result["sessions"]}
    assert {a["id"], b["id"]} == ids


async def test_session_list_filters_by_status(graph: SessionGraph) -> None:
    handlers = make_session_handlers(graph)
    s = await handlers["session.new"]({})
    await handlers["session.pause"]({"session_id": s["id"]})

    paused = await handlers["session.list"]({"status": "paused"})
    active = await handlers["session.list"]({"status": "active"})
    assert len(paused["sessions"]) == 1
    assert len(active["sessions"]) == 0


async def test_session_fork_creates_child_with_parent_pointer(graph: SessionGraph) -> None:
    handlers = make_session_handlers(graph)
    parent = await handlers["session.new"]({"intent": "root"})
    child = await handlers["session.fork"](
        {"parent_id": parent["id"], "intent": "branch"},
    )
    assert child["parent"] == parent["id"]
    assert child["intent"] == "branch"


async def test_session_pause_resume_cycle(graph: SessionGraph) -> None:
    handlers = make_session_handlers(graph)
    s = await handlers["session.new"]({})
    paused = await handlers["session.pause"]({"session_id": s["id"]})
    assert paused["status"] == "paused"
    resumed = await handlers["session.resume"]({"session_id": s["id"]})
    assert resumed["status"] == "active"


async def test_session_abort_marks_terminal(graph: SessionGraph) -> None:
    handlers = make_session_handlers(graph)
    s = await handlers["session.new"]({})
    aborted = await handlers["session.abort"]({"session_id": s["id"]})
    assert aborted["status"] == "aborted"


async def test_session_get_returns_full_session(graph: SessionGraph) -> None:
    handlers = make_session_handlers(graph)
    s = await handlers["session.new"]({"intent": "hi"})
    got = await handlers["session.get"]({"session_id": s["id"]})
    assert got == s


async def test_session_get_unknown_raises(graph: SessionGraph) -> None:
    handlers = make_session_handlers(graph)
    with pytest.raises(SessionNotFoundError):
        await handlers["session.get"]({"session_id": str(uuid4())})


async def test_session_pause_invalid_raises(graph: SessionGraph) -> None:
    handlers = make_session_handlers(graph)
    s = await handlers["session.new"]({})
    await handlers["session.pause"]({"session_id": s["id"]})
    with pytest.raises(SessionStateError):
        await handlers["session.pause"]({"session_id": s["id"]})


async def test_session_children_returns_forks(graph: SessionGraph) -> None:
    handlers = make_session_handlers(graph)
    parent = await handlers["session.new"]({})
    c1 = await handlers["session.fork"]({"parent_id": parent["id"]})
    c2 = await handlers["session.fork"]({"parent_id": parent["id"]})
    result = await handlers["session.children"]({"session_id": parent["id"]})
    ids = {s["id"] for s in result["sessions"]}
    assert ids == {c1["id"], c2["id"]}


async def test_session_add_labels_persists(graph: SessionGraph) -> None:
    from capabledeputy.policy.labels import ProvenanceLevel

    handlers = make_session_handlers(graph)
    s = await handlers["session.new"]({})
    updated = await handlers["session.add_labels"](
        {"session_id": s["id"], "labels": ["trusted.user_direct"]},
    )
    # trusted.user_direct maps to ProvenanceTag(PRINCIPAL_DIRECT)
    assert any(e["level"] == ProvenanceLevel.PRINCIPAL_DIRECT.value for e in updated["axis_b"])


async def test_session_add_labels_is_additive(graph: SessionGraph) -> None:
    from capabledeputy.policy.labels import ProvenanceLevel

    handlers = make_session_handlers(graph)
    s = await handlers["session.new"]({})
    await handlers["session.add_labels"](
        {"session_id": s["id"], "labels": ["trusted.user_direct"]},
    )
    updated = await handlers["session.add_labels"](
        {"session_id": s["id"], "labels": ["confidential.personal"]},
    )
    # trusted.user_direct maps to ProvenanceTag(PRINCIPAL_DIRECT) in axis_b
    assert any(e["level"] == ProvenanceLevel.PRINCIPAL_DIRECT.value for e in updated["axis_b"])
    # confidential.personal maps to CategoryTag("personal") in axis_a
    assert any(c["category"] == "personal" for c in updated["axis_a"])
