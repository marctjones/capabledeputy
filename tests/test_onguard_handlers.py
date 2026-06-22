from pathlib import Path

from capabledeputy.app import App
from capabledeputy.audit.events import EventType
from capabledeputy.daemon.onguard_handlers import make_onguard_handlers


async def test_onguard_handlers_audit_and_persist_coordination_state(
    tmp_path: Path,
) -> None:
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await app.startup()
    handlers = make_onguard_handlers(app)

    registered = await handlers["client.registry.register"](
        {
            "client_id": "onguard.digest.daily",
            "kind": "onguard",
            "allowed_schedules": ["daily-news"],
        }
    )
    assert registered["client"]["client_id"] == "onguard.digest.daily"

    await handlers["client.config.propose"](
        {
            "config_id": "digest-profile",
            "client_id": "onguard.digest.daily",
            "schema_name": "digest.interest_profile",
            "payload": {"topics": ["security"]},
            "labels": ["personal.profile"],
            "proposed_by": "ai",
        }
    )
    approved = await handlers["client.config.approve"](
        {"config_id": "digest-profile", "approved_by": "marc"}
    )
    assert approved["config"]["status"] == "approved"

    enqueued = await handlers["client.queue.enqueue"](
        {
            "client_id": "onguard.digest.daily",
            "command": "build_digest",
            "payload": {"date": "2026-06-21"},
            "labels": ["untrusted.external"],
            "provenance": {"source": "schedule:daily-news"},
            "created_by": "schedule",
        }
    )
    claimed = await handlers["client.queue.claim"](
        {"client_id": "onguard.digest.daily", "claimed_by": "worker"}
    )
    assert claimed["command"]["command_id"] == enqueued["command"]["command_id"]
    assert claimed["command"]["status"] == "claimed"

    completed = await handlers["client.queue.complete"](
        {
            "command_id": claimed["command"]["command_id"],
            "result": {"ok": True},
            "artifact_ref": "artifact:digest",
        }
    )
    assert completed["command"]["status"] == "completed"

    event = await handlers["client.events.publish"](
        {
            "client_id": "onguard.digest.daily",
            "command_id": claimed["command"]["command_id"],
            "event_type": "digest.ready",
            "payload": {"title": "Daily brief"},
            "labels": ["personal.profile"],
        }
    )
    assert event["event"]["event_type"] == "digest.ready"
    acked = await handlers["client.events.ack"](
        {"event_id": event["event"]["event_id"], "acknowledged_by": "marc"}
    )
    assert acked["event"]["acknowledged_by"] == "marc"

    artifact = await handlers["artifact.create"](
        {
            "artifact_id": "artifact:digest",
            "client_id": "onguard.digest.daily",
            "command_id": claimed["command"]["command_id"],
            "artifact_type": "digest",
            "payload": {"title": "Daily brief"},
            "labels": ["personal.profile"],
            "provenance": {"source": "test"},
            "created_by": "worker",
        }
    )
    assert artifact["artifact"]["labels"] == ["personal.profile"]
    assert (await handlers["artifact.read"]({"artifact_id": artifact["artifact"]["artifact_id"]}))[
        "artifact"
    ]["provenance"] == {"source": "test"}
    promoted = await handlers["artifact.promote"](
        {"artifact_id": artifact["artifact"]["artifact_id"], "promoted_by": "marc"}
    )
    assert promoted["artifact"]["status"] == "promoted"

    schedule = await handlers["schedule.create"](
        {
            "schedule_id": "daily-news",
            "client_id": "onguard.digest.daily",
            "recurrence": {"kind": "interval", "seconds": 60},
            "command": "build_digest",
            "payload": {},
            "labels": ["personal.profile"],
            "created_by": "marc",
            "approved_by": "marc",
            "next_run_at": "2026-06-21T07:00:00+00:00",
        }
    )
    assert schedule["schedule"]["status"] == "active"
    due = await handlers["schedule.claim_due"](
        {"client_id": "onguard.digest.daily", "claimed_by": "worker"}
    )
    assert due["run"] is not None
    finished = await handlers["schedule.complete_run"](
        {
            "run_id": due["run"]["run_id"],
            "result": {"ok": True},
            "artifact_ref": artifact["artifact"]["artifact_id"],
        }
    )
    assert finished["run"]["status"] == "completed"
    history = await handlers["schedule.history"]({"schedule_id": "daily-news"})
    assert history["runs"][0]["artifact_ref"] == artifact["artifact"]["artifact_id"]
    manual = await handlers["schedule.run_now"](
        {"schedule_id": "daily-news", "created_by": "marc", "command_id": "manual-run"}
    )
    assert manual["command"]["command_id"] == "manual-run"
    disabled = await handlers["schedule.disable"]({"schedule_id": "daily-news"})
    assert disabled["schedule"]["status"] == "disabled"

    events = await app.audit.read_all()
    event_types = {event.event_type for event in events}
    assert EventType.ONGUARD_CLIENT_REGISTERED in event_types
    assert EventType.ONGUARD_CONFIG_CHANGED in event_types
    assert EventType.ONGUARD_COMMAND_QUEUED in event_types
    assert EventType.ONGUARD_COMMAND_CLAIMED in event_types
    assert EventType.ONGUARD_COMMAND_FINISHED in event_types
    assert EventType.ONGUARD_EVENT_PUBLISHED in event_types
    assert EventType.ONGUARD_SCHEDULE_CHANGED in event_types
    assert EventType.ONGUARD_SCHEDULE_RUN in event_types
    assert EventType.ONGUARD_ARTIFACT_CHANGED in event_types
