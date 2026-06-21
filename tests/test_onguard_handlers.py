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

    schedule = await handlers["schedule.create"](
        {
            "schedule_id": "daily-news",
            "client_id": "onguard.digest.daily",
            "recurrence": {"kind": "daily", "hour": 7},
            "command": "build_digest",
            "payload": {},
            "labels": ["personal.profile"],
            "created_by": "marc",
            "approved_by": "marc",
        }
    )
    assert schedule["schedule"]["status"] == "active"

    events = await app.audit.read_all()
    event_types = {event.event_type for event in events}
    assert EventType.ONGUARD_CLIENT_REGISTERED in event_types
    assert EventType.ONGUARD_CONFIG_CHANGED in event_types
    assert EventType.ONGUARD_COMMAND_QUEUED in event_types
    assert EventType.ONGUARD_COMMAND_CLAIMED in event_types
    assert EventType.ONGUARD_COMMAND_FINISHED in event_types
    assert EventType.ONGUARD_EVENT_PUBLISHED in event_types
    assert EventType.ONGUARD_SCHEDULE_CHANGED in event_types
