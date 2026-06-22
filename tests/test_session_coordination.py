from pathlib import Path

from capabledeputy.app import App
from capabledeputy.approval.model import ApprovalAction
from capabledeputy.daemon.approval_handlers import make_approval_handlers
from capabledeputy.daemon.session_handlers import make_session_handlers
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import LLMResponse


async def test_session_input_submit_and_events_are_replayable(tmp_path: Path) -> None:
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=FakeLLMClient([LLMResponse(content="unused")]),
    )
    await app.startup()
    session = await app.graph.new()
    handlers = make_session_handlers(app.graph, app.session_coordinator)

    queued = await handlers["session.input.submit"](
        {
            "session_id": str(session.id),
            "message": "next question",
            "submitted_by": "swift-gui",
        },
    )
    assert queued["queued"] is True
    assert queued["input"]["submitted_by"] == "swift-gui"

    pending = await handlers["session.input.queue"]({"session_id": str(session.id)})
    assert [item["message"] for item in pending["inputs"]] == ["next question"]

    replay = await handlers["session.events"]({"session_id": str(session.id), "cursor": 0})
    assert replay["events"][0]["type"] == "input_queued"
    cursor = replay["next_cursor"]
    assert cursor > 0

    replay_after_cursor = await handlers["session.events"](
        {"session_id": str(session.id), "cursor": cursor},
    )
    assert replay_after_cursor["events"] == []
    assert replay_after_cursor["next_cursor"] == cursor


async def test_repeat_approval_decision_returns_terminal_state(tmp_path: Path) -> None:
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=FakeLLMClient([LLMResponse(content="unused")]),
    )
    await app.startup()
    session = await app.graph.new()
    handlers = make_approval_handlers(app)

    submitted = await handlers["approval.submit"](
        {
            "from_session": str(session.id),
            "action": ApprovalAction.DECLASSIFY.value,
            "payload": "approved text",
            "target": "operator",
        },
    )
    first = await handlers["approval.approve"]({"id": submitted["id"], "decided_by": "gui"})
    second = await handlers["approval.approve"]({"id": submitted["id"], "decided_by": "tui"})

    assert first["approval"]["status"] == "approved"
    assert second["already_decided"] is True
    assert second["approval"]["status"] == "approved"
    assert second["approval"]["decided_by"] == "gui"

    denied = await handlers["approval.deny"]({"id": submitted["id"], "reason": "late"})
    assert denied["already_decided"] is True
    assert denied["status"] == "approved"
