"""Daemon RPC handlers for onguard client coordination."""

from __future__ import annotations

from typing import Any

from capabledeputy.app import App
from capabledeputy.audit.events import Event, EventType
from capabledeputy.daemon.handlers import Handler


async def _audit(app: App, event_type: EventType, payload: dict[str, Any]) -> None:
    await app.audit.write(Event(event_type=event_type, payload=payload))


def make_onguard_handlers(app: App) -> dict[str, Handler]:
    async def client_register(params: dict[str, Any]) -> dict[str, Any]:
        client = await app.onguard.register_client(
            client_id=str(params["client_id"]),
            kind=str(params.get("kind", "onguard")),
            owner=params.get("owner"),
            version=params.get("version"),
            allowed_schedules=[str(v) for v in params.get("allowed_schedules", [])],
            metadata=dict(params.get("metadata") or {}),
            status=str(params.get("status", "active")),
        )
        await _audit(
            app,
            EventType.ONGUARD_CLIENT_REGISTERED,
            {"client_id": client["client_id"], "kind": client["kind"], "status": client["status"]},
        )
        return {"client": client}

    async def client_list(params: dict[str, Any]) -> dict[str, Any]:
        return {"clients": await app.onguard.list_clients(kind=params.get("kind"))}

    async def config_propose(params: dict[str, Any]) -> dict[str, Any]:
        config = await app.onguard.propose_config(
            config_id=str(params["config_id"]),
            client_id=str(params["client_id"]),
            schema_name=str(params.get("schema_name", "default")),
            payload=dict(params.get("payload") or {}),
            labels=[str(v) for v in params.get("labels", [])],
            proposed_by=str(params.get("proposed_by", "operator")),
            status=str(params.get("status", "proposed")),
        )
        await _audit(
            app,
            EventType.ONGUARD_CONFIG_CHANGED,
            {
                "config_id": config["config_id"],
                "client_id": config["client_id"],
                "status": config["status"],
                "labels": config["labels"],
            },
        )
        return {"config": config}

    async def config_approve(params: dict[str, Any]) -> dict[str, Any]:
        config = await app.onguard.approve_config(
            config_id=str(params["config_id"]),
            approved_by=str(params.get("approved_by", "operator")),
        )
        await _audit(
            app,
            EventType.ONGUARD_CONFIG_CHANGED,
            {
                "config_id": config["config_id"],
                "client_id": config["client_id"],
                "status": config["status"],
                "approved_by": config["approved_by"],
            },
        )
        return {"config": config}

    async def config_list(params: dict[str, Any]) -> dict[str, Any]:
        return {
            "configs": await app.onguard.list_configs(
                client_id=params.get("client_id"),
                status=params.get("status"),
            )
        }

    async def queue_enqueue(params: dict[str, Any]) -> dict[str, Any]:
        command = await app.onguard.enqueue_command(
            command_id=params.get("command_id"),
            client_id=str(params["client_id"]),
            command=str(params["command"]),
            payload=dict(params.get("payload") or {}),
            labels=[str(v) for v in params.get("labels", [])],
            provenance=dict(params.get("provenance") or {}),
            created_by=str(params.get("created_by", "operator")),
        )
        await _audit(
            app,
            EventType.ONGUARD_COMMAND_QUEUED,
            {
                "command_id": command["command_id"],
                "client_id": command["client_id"],
                "command": command["command"],
                "labels": command["labels"],
                "provenance": command["provenance"],
            },
        )
        return {"command": command}

    async def queue_claim(params: dict[str, Any]) -> dict[str, Any]:
        command = await app.onguard.claim_command(
            client_id=str(params["client_id"]),
            claimed_by=str(params.get("claimed_by", params["client_id"])),
            lease_seconds=int(params.get("lease_seconds", 300)),
            command_id=params.get("command_id"),
        )
        if command is None:
            return {"command": None}
        await _audit(
            app,
            EventType.ONGUARD_COMMAND_CLAIMED,
            {
                "command_id": command["command_id"],
                "client_id": command["client_id"],
                "claimed_by": command["claimed_by"],
                "lease_until": command["lease_until"],
            },
        )
        return {"command": command}

    async def queue_complete(params: dict[str, Any]) -> dict[str, Any]:
        command = await app.onguard.complete_command(
            command_id=str(params["command_id"]),
            result=dict(params.get("result") or {}),
            artifact_ref=params.get("artifact_ref"),
        )
        await _audit(
            app,
            EventType.ONGUARD_COMMAND_FINISHED,
            {
                "command_id": command["command_id"],
                "client_id": command["client_id"],
                "status": command["status"],
                "artifact_ref": command["artifact_ref"],
            },
        )
        return {"command": command}

    async def queue_fail(params: dict[str, Any]) -> dict[str, Any]:
        command = await app.onguard.fail_command(
            command_id=str(params["command_id"]),
            result=dict(params.get("result") or {}),
            artifact_ref=params.get("artifact_ref"),
        )
        await _audit(
            app,
            EventType.ONGUARD_COMMAND_FINISHED,
            {
                "command_id": command["command_id"],
                "client_id": command["client_id"],
                "status": command["status"],
                "artifact_ref": command["artifact_ref"],
            },
        )
        return {"command": command}

    async def queue_list(params: dict[str, Any]) -> dict[str, Any]:
        return {
            "commands": await app.onguard.list_commands(
                client_id=params.get("client_id"),
                status=params.get("status"),
            )
        }

    async def events_publish(params: dict[str, Any]) -> dict[str, Any]:
        event = await app.onguard.publish_event(
            event_id=params.get("event_id"),
            client_id=str(params["client_id"]),
            command_id=params.get("command_id"),
            schedule_id=params.get("schedule_id"),
            event_type=str(params["event_type"]),
            payload=dict(params.get("payload") or {}),
            labels=[str(v) for v in params.get("labels", [])],
        )
        await _audit(
            app,
            EventType.ONGUARD_EVENT_PUBLISHED,
            {
                "event_id": event["event_id"],
                "client_id": event["client_id"],
                "event_type": event["event_type"],
                "labels": event["labels"],
            },
        )
        return {"event": event}

    async def events_list(params: dict[str, Any]) -> dict[str, Any]:
        return {
            "events": await app.onguard.list_events(
                client_id=params.get("client_id"),
                limit=int(params.get("limit", 100)),
            )
        }

    async def schedule_create(params: dict[str, Any]) -> dict[str, Any]:
        schedule = await app.onguard.create_schedule(
            schedule_id=str(params["schedule_id"]),
            client_id=str(params["client_id"]),
            recurrence=dict(params.get("recurrence") or {}),
            command=str(params["command"]),
            payload=dict(params.get("payload") or {}),
            labels=[str(v) for v in params.get("labels", [])],
            created_by=str(params.get("created_by", "operator")),
            approved_by=params.get("approved_by"),
            next_run_at=params.get("next_run_at"),
            status=params.get("status"),
        )
        await _audit(
            app,
            EventType.ONGUARD_SCHEDULE_CHANGED,
            {
                "schedule_id": schedule["schedule_id"],
                "client_id": schedule["client_id"],
                "status": schedule["status"],
                "labels": schedule["labels"],
            },
        )
        return {"schedule": schedule}

    async def schedule_list(params: dict[str, Any]) -> dict[str, Any]:
        return {
            "schedules": await app.onguard.list_schedules(
                client_id=params.get("client_id"),
                status=params.get("status"),
            )
        }

    return {
        "client.registry.register": client_register,
        "client.registry.list": client_list,
        "client.config.propose": config_propose,
        "client.config.approve": config_approve,
        "client.config.list": config_list,
        "client.queue.enqueue": queue_enqueue,
        "client.queue.claim": queue_claim,
        "client.queue.complete": queue_complete,
        "client.queue.fail": queue_fail,
        "client.queue.list": queue_list,
        "client.events.publish": events_publish,
        "client.events.list": events_list,
        "schedule.create": schedule_create,
        "schedule.list": schedule_list,
    }
