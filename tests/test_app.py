from pathlib import Path

import pytest

from capabledeputy.app import App
from capabledeputy.policy.labels import CategoryTag, LabelState, Tier


@pytest.fixture
def app(tmp_path: Path) -> App:
    return App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )


async def test_startup_initializes_store(app: App, tmp_path: Path) -> None:
    await app.startup()
    assert (tmp_path / "state.db").exists()


async def test_startup_loads_persisted_sessions(tmp_path: Path) -> None:
    state_db = tmp_path / "state.db"
    audit_log = tmp_path / "audit.jsonl"

    app1 = App(state_db_path=state_db, audit_log_path=audit_log)
    await app1.startup()
    s = await app1.graph.new(intent="hello")

    app2 = App(state_db_path=state_db, audit_log_path=audit_log)
    await app2.startup()
    assert s.id in app2.graph
    assert app2.graph.get(s.id).intent == "hello"


async def test_startup_loads_persisted_memory(tmp_path: Path) -> None:
    state_db = tmp_path / "state.db"
    audit_log = tmp_path / "audit.jsonl"
    label_state = LabelState(
        a=frozenset(
            {
                CategoryTag(
                    "personal",
                    Tier.REGULATED,
                    assignment_provenance="source-declared",
                )
            }
        )
    )

    app1 = App(state_db_path=state_db, audit_log_path=audit_log)
    await app1.startup()
    app1.memory.write("preference.default-calendar", "Work", label_state)

    app2 = App(state_db_path=state_db, audit_log_path=audit_log)
    await app2.startup()

    entry = app2.memory.read("preference.default-calendar")
    assert entry is not None
    assert entry.value == "Work"
    assert entry.label_state == label_state


async def test_startup_is_idempotent(app: App) -> None:
    await app.startup()
    await app.startup()


async def test_default_paths_under_xdg_data_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    app = App()
    assert app.store.path == tmp_path / "capabledeputy" / "state.db"
    assert app.audit.path == tmp_path / "capabledeputy" / "audit.jsonl"
