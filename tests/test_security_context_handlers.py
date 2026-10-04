from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from capabledeputy.app import App
from capabledeputy.approval.model import ApprovalAction
from capabledeputy.audit.events import Event, EventType
from capabledeputy.daemon.security_context_handlers import make_security_context_handlers
from capabledeputy.policy.labels import tags_for_labels_strings
from capabledeputy.upstream.manager import UpstreamServerStatus


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
        await app.audit.write(
            Event(
                event_type=EventType.TOOL_SURFACE_SELECTED,
                session_id=session.id,
                payload={
                    "method": "capability_filtered",
                    "n_visible": 4,
                    "n_selected": 2,
                    "selected": ["gmail.messages.list", "web.fetch"],
                    "mandatory_added": ["web.fetch"],
                    "selection_reason": "intent match",
                },
            ),
        )
        await app.audit.write(
            Event(
                event_type=EventType.LABEL_PROPAGATED,
                session_id=session.id,
                payload={
                    "source_tool": "web.fetch",
                    "categories_added": [],
                    "levels_added": ["external-untrusted"],
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
        propagation = ctx["labels"]["propagation"]
        assert len(propagation) == 1
        assert propagation[0]["source_tool"] == "web.fetch"
        assert propagation[0]["categories_added"] == []
        assert propagation[0]["levels_added"] == ["external-untrusted"]
        assert ctx["approvals"]["pending_count"] == 1
        assert ctx["policy"]["decision_count"] == 1
        assert ctx["policy"]["approval_gate_count"] == 1
        assert ctx["policy"]["matched_rule_ids"] == ["rule.untrusted_egress"]
        assert ctx["provenance"]["node_count"] >= 1
        assert ctx["actors"]["onguard"]["client"]["client_id"] == "onguard.digest.daily"
        assert ctx["actors"]["onguard"]["commands"][0]["command_id"] == command["command_id"]
        selection = ctx["actors"]["tool_surface"]["recent_selections"][0]
        assert selection["selected"] == ["gmail.messages.list", "web.fetch"]
        assert selection["n_visible"] == 4
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


async def test_session_security_context_includes_upstream_mcp_server_status(
    tmp_path: Path,
) -> None:
    app = App(state_db_path=tmp_path / "state.db", audit_log_path=tmp_path / "audit.jsonl")
    await app.startup()
    try:
        session = await app.graph.new(owner="operator", intent="inspect upstreams")

        class FakeUpstreamManager:
            @property
            def server_status(self) -> dict[str, UpstreamServerStatus]:
                return {
                    "gmail": UpstreamServerStatus(
                        name="gmail",
                        state="registered",
                        registered_at_epoch=1,
                        registered_tool_count=3,
                        rejected_tool_count=1,
                        transport="stdio",
                    ),
                }

        cast(Any, app).upstream_manager = FakeUpstreamManager()
        handlers = make_security_context_handlers(app)

        ctx = await handlers["session.security_context"]({"session_id": str(session.id)})

        assert ctx["actors"]["external_mcp"] == [
            {
                "name": "gmail",
                "state": "registered",
                "transport": "stdio",
                "registered_tool_count": 3,
                "rejected_tool_count": 1,
            },
        ]
    finally:
        await app.shutdown()


async def test_session_security_context_pages_provenance_without_losing_counts(
    tmp_path: Path,
) -> None:
    app = App(state_db_path=tmp_path / "state.db", audit_log_path=tmp_path / "audit.jsonl")
    await app.startup()
    try:
        session = await app.graph.new(owner="operator", intent="paged provenance")
        # Match the reported daily-use graph scale.  The response must remain
        # bounded even though the daemon owns thousands of persisted nodes.
        for index in range(4_001):
            await app.audit.write(
                Event(
                    event_type=EventType.PROVENANCE_NODE,
                    session_id=session.id,
                    payload={
                            "node_id": f"node-{index:04}",
                        "kind": "tool" if index % 2 else "source",
                        "materialized_id": f"materialized-{index}",
                    },
                ),
            )
        handlers = make_security_context_handlers(app)

        ctx = await handlers["session.security_context"](
            {"session_id": str(session.id), "provenance_offset": 4_000, "provenance_limit": 20},
        )

        provenance = ctx["provenance"]
        assert provenance["node_count"] == 4_001
        assert provenance["node_offset"] == 4_000
        assert provenance["node_limit"] == 20
        assert provenance["node_returned"] == 1
        assert provenance["node_has_more"] is False
        assert [node["id"] for node in provenance["nodes"]] == ["node-4000"]
    finally:
        await app.shutdown()
