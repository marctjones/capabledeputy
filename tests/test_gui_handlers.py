from pathlib import Path

import pytest

from capabledeputy.app import App
from capabledeputy.approval.model import ApprovalAction
from capabledeputy.audit.events import Event, EventType
from capabledeputy.daemon.gui_handlers import make_gui_handlers


@pytest.fixture
def app(tmp_path: Path) -> App:
    return App(state_db_path=tmp_path / "state.db", audit_log_path=tmp_path / "audit.jsonl")


async def test_app_status_reports_daemon_gui_summary(app: App) -> None:
    handlers = make_gui_handlers(app)
    session = await app.graph.new(intent="hello", purpose_handle="inbox")
    await app.approval_queue.submit(
        from_session=session.id,
        action=ApprovalAction.SEND_EMAIL,
        payload="hi",
        target="me@example.com",
        justification="test",
    )

    result = await handlers["app.status"]({})

    assert result["daemon"]["connected"] is True
    assert result["daemon"]["session_count"] == 1
    assert result["daemon"]["pending_approval_count"] == 1
    assert result["daemon"]["tool_count"] > 0


async def test_setup_status_reports_actionable_checks(app: App) -> None:
    handlers = make_gui_handlers(app)

    result = await handlers["setup.status"]({})

    check_ids = {check["id"] for check in result["checks"]}
    assert {
        "daemon",
        "model",
        "relationship-groups",
        "apple-automation",
        "daemon-settings",
        "config-validation",
    } <= check_ids


async def test_policy_explain_returns_plain_english_for_recent_decision(app: App) -> None:
    handlers = make_gui_handlers(app)
    session = await app.graph.new(intent="policy")
    await app.audit.write(
        Event(
            event_type=EventType.POLICY_DECIDED,
            session_id=session.id,
            payload={
                "decision": "deny",
                "rule": "no matching capability",
                "reason": "no matching capability for SEND_EMAIL",
            },
        ),
    )

    result = await handlers["policy.explain"]({"session_id": str(session.id)})

    assert result["found"] is True
    assert result["decision"] == "deny"
    assert "not granted authority" in result["plain_english"]


async def test_approval_detail_reports_daemon_action_guidance(app: App) -> None:
    handlers = make_gui_handlers(app)
    session = await app.graph.new(intent="approval")
    await app.approval_queue.submit(
        from_session=session.id,
        action=ApprovalAction.SEND_EMAIL,
        payload="hello",
        target="spouse@example.com",
        justification="trusted recurring recipient",
        rule="untrusted-meets-egress",
    )
    second = await app.approval_queue.submit(
        from_session=session.id,
        action=ApprovalAction.SEND_EMAIL,
        payload="second",
        target="spouse@example.com",
        justification="same target",
    )

    result = await handlers["approval.detail"]({"id": second.id})

    assert result["approval"]["id"] == second.id
    assert result["approval"]["action"] == "SEND_EMAIL"
    assert "Send an email" in result["effect_text"]
    assert result["sibling_group"]["id"] == str(second.sibling_group_id)
    assert result["sibling_group"]["approvable"] is True
    assert {"deny", "defer", "narrow-pattern"} <= {
        action["id"] for action in result["suggested_actions"]
    }


async def test_provenance_graph_materializes_nodes_and_edges(app: App) -> None:
    handlers = make_gui_handlers(app)
    session = await app.graph.new(intent="prov")
    await app.audit.write(
        Event(
            event_type=EventType.PROVENANCE_NODE,
            session_id=session.id,
            payload={"node_id": "a", "kind": "source"},
        ),
    )
    await app.audit.write(
        Event(
            event_type=EventType.PROVENANCE_NODE,
            session_id=session.id,
            payload={"node_id": "b", "kind": "tool_result"},
        ),
    )
    await app.audit.write(
        Event(
            event_type=EventType.PROVENANCE_EDGE,
            session_id=session.id,
            payload={"from_node_id": "a", "to_node_id": "b", "kind": "input"},
        ),
    )

    result = await handlers["provenance.graph"]({"session_id": str(session.id)})

    assert {node["id"] for node in result["nodes"]} == {"a", "b"}
    assert result["edges"] == [{"from": "a", "to": "b", "kind": "input", "metadata": {}}]
