from __future__ import annotations

from pathlib import Path

from capabledeputy.app import App
from capabledeputy.daemon.approval_handlers import make_approval_handlers
from capabledeputy.daemon.onguard_handlers import make_onguard_handlers


async def _app(tmp_path: Path) -> tuple[App, dict]:
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await app.startup()
    handlers = make_onguard_handlers(app)
    await handlers["client.registry.register"](
        {"client_id": "onguard.digest.daily", "kind": "onguard"},
    )
    return app, handlers


async def test_onguard_notifications_contract_and_list(tmp_path: Path) -> None:
    _app_obj, handlers = await _app(tmp_path)
    event = await handlers["client.events.publish"](
        {
            "event_id": "event-digest-ready",
            "client_id": "onguard.digest.daily",
            "event_type": "digest.ready",
            "payload": {
                "title": "Daily digest ready",
                "summary": "Three reviewed items are ready.",
                "artifact_ref": "artifact:digest",
            },
            "labels": ["personal.profile"],
        }
    )

    notifications = await handlers["onguard.notifications.list"]({})

    assert notifications["contract"]["dedupe_key"] == "event_id"
    assert notifications["notifications"][0]["id"] == event["event"]["event_id"]
    assert notifications["notifications"][0]["deep_link"] == "capdep://onguard/event-digest-ready"
    assert notifications["notifications"][0]["artifact_ref"] == "artifact:digest"


async def test_approval_digest_binds_exact_payload_hashes(tmp_path: Path) -> None:
    app, handlers = await _app(tmp_path)
    approval_handlers = make_approval_handlers(app)
    session = await app.graph.new(intent="background approval source")
    submitted = await approval_handlers["approval.submit"](
        {
            "from_session": str(session.id),
            "action": "SEND_EMAIL",
            "payload": "send this exact body",
            "target": "person@example.com",
            "labels_in": ["confidential.personal"],
            "labels_out": ["egress.email"],
            "justification": "background digest follow-up",
        }
    )

    digest = await handlers["onguard.approval_digest"]({})

    assert digest["pending_count"] == 1
    item = digest["groups"][0]["items"][0]
    assert item["id"] == submitted["id"]
    assert item["target"] == "person@example.com"
    assert len(item["payload_sha256"]) == 64


async def test_artifact_handoff_creates_labeled_interactive_session(tmp_path: Path) -> None:
    _app_obj, handlers = await _app(tmp_path)
    artifact = await handlers["artifact.create"](
        {
            "artifact_id": "artifact:digest",
            "client_id": "onguard.digest.daily",
            "artifact_type": "digest",
            "payload": {"title": "Daily brief"},
            "labels": ["confidential.personal"],
            "provenance": {"source": "onguard"},
            "created_by": "worker",
        }
    )

    handoff = await handlers["artifact.handoff"](
        {"artifact_id": artifact["artifact"]["artifact_id"]},
    )

    session = handoff["session"]
    assert session["origin"]["kind"] == "onguard_handoff"
    assert session["origin"]["metadata"]["artifact_id"] == "artifact:digest"
    assert [tag["category"] for tag in session["label_state"]["a"]] == ["personal"]
    assert "Daily brief" in session["history"][-1]["content"]
