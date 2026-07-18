"""#325 — the native task list is a REAL local persistent store (SQLite), so a
reminder survives a daemon restart. No external backend / credentials."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4

from capabledeputy.policy.labels import LabelState
from capabledeputy.tools.native.tasks import TaskStore, make_tasks_tools
from capabledeputy.tools.registry import ToolContext


def _ctx() -> ToolContext:
    return ToolContext(session_id=uuid4(), label_state=LabelState())


# --- store CRUD --------------------------------------------------------------


def test_add_get_and_all_ordering() -> None:
    store = TaskStore()  # in-memory
    a = store.add("buy milk", notes="2%")
    b = store.add("call dentist")
    assert store.get(a.id) is not None
    assert store.get(a.id).notes == "2%"  # type: ignore[union-attr]
    assert [t.id for t in store.all()] == [a.id, b.id]  # created order
    assert store.get("nope") is None


def test_complete_edit_remove_return_bools() -> None:
    store = TaskStore()
    t = store.add("task")
    assert store.complete(t.id) is True
    assert store.get(t.id).done is True  # type: ignore[union-attr]
    assert store.complete("missing") is False

    assert store.edit(t.id, title="renamed", notes="new notes") is True
    assert store.get(t.id).title == "renamed"  # type: ignore[union-attr]
    assert store.get(t.id).notes == "new notes"  # type: ignore[union-attr]
    assert store.edit(t.id) is True  # no-field edit is a no-op success
    assert store.edit("missing", title="x") is False

    assert store.remove(t.id) is True
    assert store.remove(t.id) is False  # already gone


# --- persistence (the #325 point) --------------------------------------------


def test_tasks_survive_a_reopen(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    first = TaskStore(db)
    made = first.add("water the plants", notes="every tuesday")

    # A fresh store on the same file (as if the daemon restarted) sees it.
    reopened = TaskStore(db)
    got = reopened.get(made.id)
    assert got is not None
    assert got.title == "water the plants"
    assert got.notes == "every tuesday"


def test_table_is_additive_on_a_shared_db(tmp_path: Path) -> None:
    # The tasks table coexists with whatever else already lives in state.db
    # (sessions, onguard, …) — CREATE TABLE IF NOT EXISTS never clobbers.
    db = tmp_path / "state.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE sessions (id TEXT)")
    con.execute("INSERT INTO sessions (id) VALUES ('pre-existing')")
    con.commit()
    con.close()

    store = TaskStore(db)
    store.add("coexists")
    con = sqlite3.connect(db)
    assert con.execute("SELECT id FROM sessions").fetchone()[0] == "pre-existing"
    assert con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
    con.close()


# --- handler integration -----------------------------------------------------


async def test_tools_round_trip_through_the_store(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "state.db")
    tools = {t.name: t for t in make_tasks_tools(store)}

    added = await tools["tasks.add"].handler({"title": "ship #325", "notes": "local store"}, _ctx())
    tid = added.output["id"]

    listed = await tools["tasks.list"].handler({}, _ctx())
    assert any(t["id"] == tid for t in listed.output["tasks"])

    edited = await tools["tasks.edit"].handler({"id": tid, "notes": "revised"}, _ctx())
    assert edited.output["edited"] is True

    done = await tools["tasks.complete"].handler({"id": tid}, _ctx())
    assert done.output  # completed
    # After completing, default list (done excluded) no longer shows it.
    listed2 = await tools["tasks.list"].handler({}, _ctx())
    assert not any(t["id"] == tid for t in listed2.output["tasks"])

    deleted = await tools["tasks.delete"].handler({"id": tid}, _ctx())
    assert deleted.output["deleted"] is True
