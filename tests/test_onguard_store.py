from pathlib import Path

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
    assert (await restarted.list_events(client_id="onguard.digest.daily"))[0]["event_id"] == event[
        "event_id"
    ]
    assert (await restarted.list_schedules(status="active"))[0]["schedule_id"] == "daily-news"


async def test_claim_command_returns_none_when_queue_empty(tmp_path: Path) -> None:
    store = OnguardStore(tmp_path / "state.db")
    await store.register_client(client_id="onguard.empty", kind="onguard")

    claimed = await store.claim_command(client_id="onguard.empty", claimed_by="worker")

    assert claimed is None
