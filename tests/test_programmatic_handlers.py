"""Daemon RPC handlers for programmatic mode."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from capabledeputy.app import App
from capabledeputy.daemon.programmatic_handlers import make_programmatic_handlers
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import CategoryTag, LabelState
from capabledeputy.policy.tiers import Tier


async def test_dry_run_handler_returns_predicted_calls(tmp_path: Path) -> None:
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await app.startup()
    handlers = make_programmatic_handlers(app)
    result = await handlers["programmatic.dry_run"](
        {"source": 'note = call("memory.read", key="x")\n'},
    )
    assert result["ok"] is True
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["tool"] == "memory.read"


async def test_dry_run_handler_reports_parse_error(tmp_path: Path) -> None:
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await app.startup()
    handlers = make_programmatic_handlers(app)
    result = await handlers["programmatic.dry_run"]({"source": "import os\n"})
    assert result["ok"] is False
    assert result["parse_error"] is not None


async def test_run_handler_executes_against_session(tmp_path: Path) -> None:
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await app.startup()
    s = await app.graph.new(intent="programmatic handler test")
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))
    app.memory.write("k", "v", LabelState())

    handlers = make_programmatic_handlers(app)
    result = await handlers["programmatic.run"](
        {
            "source": 'r = call("memory.read", key="k")\nreturn r["value"]\n',
            "session_id": str(s.id),
        },
    )
    assert result["ok"] is True
    assert result["return_value"] is not None
    assert result["return_value"]["raw"] == "v"
    assert "label_state" in result["return_value"]


async def test_run_handler_redacts_labeled_return_value(tmp_path: Path) -> None:
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await app.startup()
    s = await app.graph.new(intent="programmatic handler labeled return test")
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))
    app.memory.write(
        "labs",
        "BP=120/80",
        LabelState(
            a=frozenset(
                {CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared")}
            ),
        ),
    )

    handlers = make_programmatic_handlers(app)
    result = await handlers["programmatic.run"](
        {
            "source": 'r = call("memory.read", key="labs")\nreturn r\n',
            "session_id": str(s.id),
        },
    )

    assert result["ok"] is True
    assert result["return_value"] is not None
    assert result["return_value"]["redacted"] is True
    assert result["return_value"]["raw"] is None
    assert "health:regulated" in result["return_value"]["labels"]["axis_a"]


async def test_run_handler_parse_error_returned(tmp_path: Path) -> None:
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await app.startup()
    s = await app.graph.new()
    handlers = make_programmatic_handlers(app)
    result = await handlers["programmatic.run"](
        {"source": "import os\n", "session_id": str(s.id)},
    )
    assert result["ok"] is False
    assert result["parse_error"] is not None
