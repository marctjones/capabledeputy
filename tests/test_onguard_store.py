from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from capabledeputy.onguard import OnguardStore


async def test_onguard_registry_config_queue_event_schedule_round_trip(
    tmp_path: Path,
) -> None:
    store = OnguardStore(tmp_path / "state.db")

    client = await store.register_client(
        client_id="onguard.digest.daily",
        kind="onguard",
        owner="marc",
        version="1.0",
        allowed_schedules=["daily-news"],
        metadata={"purpose": "daily digest"},
    )
    assert client["client_id"] == "onguard.digest.daily"
    assert client["allowed_schedules"] == ["daily-news"]

    config = await store.propose_config(
        config_id="digest-profile",
        client_id="onguard.digest.daily",
        schema_name="digest.interest_profile",
        payload={"topics": ["security"]},
        labels=["personal.profile"],
        proposed_by="ai",
    )
    assert config["status"] == "proposed"
    approved = await store.approve_config(config_id="digest-profile", approved_by="marc")
    assert approved["status"] == "approved"
    assert approved["approved_by"] == "marc"

    command = await store.enqueue_command(
        client_id="onguard.digest.daily",
        command="build_digest",
        payload={"date": "2026-06-21"},
        labels=["untrusted.external"],
        provenance={"source": "schedule:daily-news"},
        created_by="schedule",
        command_id="cmd-1",
    )
    assert command["status"] == "queued"

    claimed = await store.claim_command(
        client_id="onguard.digest.daily",
        claimed_by="worker-1",
        lease_seconds=60,
    )
    assert claimed is not None
    assert claimed["command_id"] == "cmd-1"
    assert claimed["status"] == "claimed"
    assert claimed["attempts"] == 1
    assert claimed["lease_until"] is not None

    completed = await store.complete_command(
        command_id="cmd-1",
        result={"ok": True},
        artifact_ref="artifact:digest:2026-06-21",
    )
    assert completed["status"] == "completed"
    assert completed["result"] == {"ok": True}
    assert completed["artifact_ref"] == "artifact:digest:2026-06-21"

    event = await store.publish_event(
        client_id="onguard.digest.daily",
        command_id="cmd-1",
        event_type="digest.ready",
        payload={"title": "Daily brief"},
        labels=["personal.profile"],
    )
    assert event["event_type"] == "digest.ready"
    assert event["acknowledged_by"] is None
    acked = await store.acknowledge_event(event_id=event["event_id"], acknowledged_by="marc")
    assert acked["acknowledged_by"] == "marc"

    schedule = await store.create_schedule(
        schedule_id="daily-news",
        client_id="onguard.digest.daily",
        recurrence={"kind": "daily", "hour": 7},
        command="build_digest",
        payload={},
        labels=["personal.profile"],
        created_by="marc",
        approved_by="marc",
    )
    assert schedule["status"] == "active"
    assert schedule["recurrence"] == {"kind": "daily", "hour": 7}

    restarted = OnguardStore(tmp_path / "state.db")
    assert len(await restarted.list_clients()) == 1
    assert (await restarted.list_configs())[0]["status"] == "approved"
    assert (await restarted.list_commands(status="completed"))[0]["command_id"] == "cmd-1"
    stored_events = await restarted.list_events(client_id="onguard.digest.daily")
    assert stored_events[0]["event_id"] == event["event_id"]
    assert stored_events[0]["acknowledged_by"] == "marc"
    assert (await restarted.list_schedules(status="active"))[0]["schedule_id"] == "daily-news"


async def test_claim_command_returns_none_when_queue_empty(tmp_path: Path) -> None:
    store = OnguardStore(tmp_path / "state.db")
    await store.register_client(client_id="onguard.empty", kind="onguard")

    claimed = await store.claim_command(client_id="onguard.empty", claimed_by="worker")

    assert claimed is None


async def test_artifacts_preserve_labels_provenance_and_delete_safely(tmp_path: Path) -> None:
    store = OnguardStore(tmp_path / "state.db")
    await store.register_client(client_id="onguard.digest.daily", kind="onguard")

    artifact = await store.create_artifact(
        artifact_id="artifact-1",
        client_id="onguard.digest.daily",
        artifact_type="digest",
        payload={"summary": "private"},
        labels=["personal.profile", "untrusted.external"],
        provenance={"source": "kagi-news"},
        created_by="worker",
        session_id="session-1",
    )

    assert artifact["labels"] == ["personal.profile", "untrusted.external"]
    assert artifact["provenance"] == {"source": "kagi-news"}
    assert artifact["status"] == "draft"
    assert (await store.read_artifact(artifact_id="artifact-1"))["session_id"] == "session-1"

    promoted = await store.promote_artifact(artifact_id="artifact-1", promoted_by="marc")
    assert promoted["status"] == "promoted"
    assert promoted["promoted_by"] == "marc"
    assert [item["artifact_id"] for item in await store.list_artifacts(status="promoted")] == [
        "artifact-1"
    ]

    deleted = await store.delete_artifact(artifact_id="artifact-1")
    assert deleted["status"] == "deleted"
    assert await store.list_artifacts() == []
    with pytest.raises(KeyError):
        await store.read_artifact(artifact_id="artifact-1")


async def test_schedule_claims_are_leased_and_disabled_schedules_are_inert(
    tmp_path: Path,
) -> None:
    store = OnguardStore(tmp_path / "state.db")
    await store.register_client(client_id="onguard.digest.daily", kind="onguard")
    due = datetime(2026, 6, 21, 7, tzinfo=UTC)
    await store.create_schedule(
        schedule_id="daily-news",
        client_id="onguard.digest.daily",
        recurrence={"kind": "interval", "seconds": 3600},
        command="build_digest",
        payload={"date": "today"},
        labels=["personal.profile"],
        created_by="marc",
        approved_by="marc",
        next_run_at=due.isoformat(),
    )

    first = await store.claim_due_schedule(
        client_id="onguard.digest.daily",
        claimed_by="worker-1",
        lease_seconds=300,
        now=due + timedelta(minutes=1),
    )
    assert first is not None
    assert first["status"] == "claimed"
    assert first["run_after"] == due.isoformat()

    duplicate = await store.claim_due_schedule(
        client_id="onguard.digest.daily",
        claimed_by="worker-2",
        lease_seconds=300,
        now=due + timedelta(minutes=2),
    )
    assert duplicate is None

    completed = await store.complete_schedule_run(
        run_id=first["run_id"],
        result={"ok": True},
        artifact_ref="artifact-1",
    )
    assert completed["status"] == "completed"
    history = await store.schedule_history(schedule_id="daily-news")
    assert history[0]["artifact_ref"] == "artifact-1"

    await store.disable_schedule(schedule_id="daily-news")
    disabled_claim = await store.claim_due_schedule(
        client_id="onguard.digest.daily",
        claimed_by="worker-3",
        now=due + timedelta(hours=2),
    )
    assert disabled_claim is None


async def test_run_schedule_now_preserves_normal_queue_claim_flow(tmp_path: Path) -> None:
    store = OnguardStore(tmp_path / "state.db")
    await store.register_client(client_id="onguard.digest.daily", kind="onguard")
    await store.create_schedule(
        schedule_id="daily-news",
        client_id="onguard.digest.daily",
        recurrence={"kind": "manual"},
        command="build_digest",
        payload={"date": "today"},
        labels=["personal.profile"],
        created_by="marc",
        approved_by="marc",
    )

    command = await store.run_schedule_now(
        schedule_id="daily-news",
        created_by="marc",
        command_id="manual-run",
    )
    assert command["status"] == "queued"
    assert command["provenance"] == {"source": "schedule:daily-news", "run_now": True}

    claimed = await store.claim_command(client_id="onguard.digest.daily", claimed_by="worker")
    assert claimed is not None
    assert claimed["command_id"] == "manual-run"

    await store.disable_schedule(schedule_id="daily-news")
    with pytest.raises(RuntimeError):
        await store.run_schedule_now(schedule_id="daily-news", created_by="marc")
