from __future__ import annotations

from pathlib import Path

from capabledeputy.app import App
from capabledeputy.approval.model import ApprovalAction
from capabledeputy.audit.events import Event, EventType
from capabledeputy.daemon.security_context_handlers import make_security_context_handlers
from capabledeputy.policy.labels import tags_for_labels_strings


async def test_session_security_context_materializes_daemon_security_state(
    tmp_path: Path,
) -> None:
    app = App(state_db_path=tmp_path / "state.db", audit_log_path=tmp_path / "audit.jsonl")
    await app.startup()
    try:
        await app.onguard.register_client(
            client_id="onguard.digest.daily",
            kind="onguard",
            owner="operator",
        )
        command = await app.onguard.enqueue_command(
            client_id="onguard.digest.daily",
            command="build_daily_digest",
            payload={"topics": ["mail"]},
            labels=["external-untrusted"],
            provenance={"submitted_by": "test"},
            created_by="operator",
        )
        session = await app.graph.new(
            owner="operator",
            intent="daily digest",
            purpose_handle="personal_assistant",
            origin={
                "kind": "onguard_queued_command",
                "client_id": "onguard.digest.daily",
                "command_id": command["command_id"],
                "metadata": {"reason": "scheduled digest"},
            },
        )
        await app.graph.add_tags(
            session.id,
            tags_for_labels_strings(frozenset({"untrusted.external"})),
        )
        await app.approval_queue.submit(
            from_session=session.id,
            action=ApprovalAction.SEND_EMAIL,
            payload='{"to":"user@example.com"}',
            target="user@example.com",
            labels_in=tags_for_labels_strings(frozenset({"untrusted.external"})),
            justification="digest wants egress",
            rule="untrusted-meets-egress",
        )
        await app.audit.write(
            Event(
                event_type=EventType.POLICY_DECIDED,
                session_id=session.id,
                payload={
                    "tool": "gmail.send",
                    "decision": "require_approval",
                    "rule": "untrusted-meets-egress",
                    "reason": "untrusted content cannot directly egress",
                    "effect_class": "network_egress",
                    "v2_matched_rule_ids": ["rule.untrusted_egress"],
                },
            ),
        )

        handlers = make_security_context_handlers(app)
        ctx = await handlers["session.security_context"]({"session_id": str(session.id)})

        assert ctx["schema_version"] == 1
        assert ctx["session"]["id"] == str(session.id)
        assert ctx["session"]["purpose_handle"] == "personal_assistant"
        assert ctx["origin"]["kind"] == "onguard_queued_command"
        assert "untrusted.external" in ctx["labels"]["legacy_label_set"]
        assert ctx["approvals"]["pending_count"] == 1
        assert ctx["policy"]["decision_count"] == 1
        assert ctx["policy"]["approval_gate_count"] == 1
        assert ctx["policy"]["matched_rule_ids"] == ["rule.untrusted_egress"]
        assert ctx["provenance"]["node_count"] >= 1
        assert ctx["actors"]["onguard"]["client"]["client_id"] == "onguard.digest.daily"
        assert ctx["actors"]["onguard"]["commands"][0]["command_id"] == command["command_id"]
        assert any(
            model["name"] == "materialized_provenance_dag" and model["implemented"]
            for model in ctx["security_models"]
        )
        assert any(
            pattern["name"] == "human_approval_gate" and pattern["active"]
            for pattern in ctx["flow_patterns"]
        )
    finally:
        await app.shutdown()
