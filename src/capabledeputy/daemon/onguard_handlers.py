"""Daemon RPC handlers for onguard client coordination."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from capabledeputy.app import App
from capabledeputy.audit.events import Event, EventType
from capabledeputy.daemon.handlers import Handler
from capabledeputy.session.model import Turn


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

    async def events_ack(params: dict[str, Any]) -> dict[str, Any]:
        event = await app.onguard.acknowledge_event(
            event_id=str(params["event_id"]),
            acknowledged_by=str(params.get("acknowledged_by", "operator")),
        )
        await _audit(
            app,
            EventType.ONGUARD_EVENT_PUBLISHED,
            {
                "event_id": event["event_id"],
                "client_id": event["client_id"],
                "acknowledged_by": event["acknowledged_by"],
            },
        )
        return {"event": event}

    async def artifact_create(params: dict[str, Any]) -> dict[str, Any]:
        artifact = await app.onguard.create_artifact(
            artifact_id=params.get("artifact_id"),
            client_id=str(params["client_id"]),
            command_id=params.get("command_id"),
            schedule_id=params.get("schedule_id"),
            session_id=params.get("session_id"),
            artifact_type=str(params.get("artifact_type", "document")),
            payload=dict(params.get("payload") or {}),
            labels=[str(v) for v in params.get("labels", [])],
            provenance=dict(params.get("provenance") or {}),
            created_by=str(params.get("created_by", "operator")),
            status=str(params.get("status", "draft")),
        )
        await _audit(
            app,
            EventType.ONGUARD_ARTIFACT_CHANGED,
            {
                "artifact_id": artifact["artifact_id"],
                "client_id": artifact["client_id"],
                "status": artifact["status"],
                "labels": artifact["labels"],
                "provenance": artifact["provenance"],
            },
        )
        return {"artifact": artifact}

    async def artifact_read(params: dict[str, Any]) -> dict[str, Any]:
        return {"artifact": await app.onguard.read_artifact(artifact_id=str(params["artifact_id"]))}

    async def artifact_list(params: dict[str, Any]) -> dict[str, Any]:
        return {
            "artifacts": await app.onguard.list_artifacts(
                client_id=params.get("client_id"),
                status=params.get("status"),
            )
        }

    async def artifact_promote(params: dict[str, Any]) -> dict[str, Any]:
        artifact = await app.onguard.promote_artifact(
            artifact_id=str(params["artifact_id"]),
            promoted_by=str(params.get("promoted_by", "operator")),
            status=str(params.get("status", "promoted")),
        )
        await _audit(
            app,
            EventType.ONGUARD_ARTIFACT_CHANGED,
            {
                "artifact_id": artifact["artifact_id"],
                "client_id": artifact["client_id"],
                "status": artifact["status"],
                "promoted_by": artifact["promoted_by"],
            },
        )
        return {"artifact": artifact}

    async def artifact_delete(params: dict[str, Any]) -> dict[str, Any]:
        artifact = await app.onguard.delete_artifact(artifact_id=str(params["artifact_id"]))
        await _audit(
            app,
            EventType.ONGUARD_ARTIFACT_CHANGED,
            {
                "artifact_id": artifact["artifact_id"],
                "client_id": artifact["client_id"],
                "status": artifact["status"],
            },
        )
        return {"artifact": artifact}

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

    async def schedule_update(params: dict[str, Any]) -> dict[str, Any]:
        schedule = await app.onguard.update_schedule(
            schedule_id=str(params["schedule_id"]),
            recurrence=dict(params["recurrence"]) if "recurrence" in params else None,
            payload=dict(params["payload"]) if "payload" in params else None,
            labels=[str(v) for v in params["labels"]] if "labels" in params else None,
            status=str(params["status"]) if "status" in params else None,
            next_run_at=params.get("next_run_at"),
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

    async def schedule_disable(params: dict[str, Any]) -> dict[str, Any]:
        schedule = await app.onguard.disable_schedule(schedule_id=str(params["schedule_id"]))
        await _audit(
            app,
            EventType.ONGUARD_SCHEDULE_CHANGED,
            {
                "schedule_id": schedule["schedule_id"],
                "client_id": schedule["client_id"],
                "status": schedule["status"],
            },
        )
        return {"schedule": schedule}

    async def schedule_run_now(params: dict[str, Any]) -> dict[str, Any]:
        command = await app.onguard.run_schedule_now(
            schedule_id=str(params["schedule_id"]),
            created_by=str(params.get("created_by", "operator")),
            command_id=params.get("command_id"),
        )
        await _audit(
            app,
            EventType.ONGUARD_SCHEDULE_RUN,
            {
                "schedule_id": params["schedule_id"],
                "client_id": command["client_id"],
                "command_id": command["command_id"],
                "status": command["status"],
            },
        )
        return {"command": command}

    async def schedule_claim_due(params: dict[str, Any]) -> dict[str, Any]:
        run = await app.onguard.claim_due_schedule(
            client_id=str(params["client_id"]),
            claimed_by=str(params.get("claimed_by", params["client_id"])),
            lease_seconds=int(params.get("lease_seconds", 300)),
        )
        if run is None:
            return {"run": None}
        await _audit(
            app,
            EventType.ONGUARD_SCHEDULE_RUN,
            {
                "schedule_id": run["schedule_id"],
                "client_id": run["client_id"],
                "run_id": run["run_id"],
                "status": run["status"],
            },
        )
        return {"run": run}

    async def schedule_complete_run(params: dict[str, Any]) -> dict[str, Any]:
        run = await app.onguard.complete_schedule_run(
            run_id=str(params["run_id"]),
            result=dict(params.get("result") or {}),
            artifact_ref=params.get("artifact_ref"),
            command_id=params.get("command_id"),
            next_run_at=params.get("next_run_at"),
        )
        await _audit(
            app,
            EventType.ONGUARD_SCHEDULE_RUN,
            {
                "schedule_id": run["schedule_id"],
                "client_id": run["client_id"],
                "run_id": run["run_id"],
                "status": run["status"],
                "artifact_ref": run["artifact_ref"],
            },
        )
        return {"run": run}

    async def schedule_fail_run(params: dict[str, Any]) -> dict[str, Any]:
        run = await app.onguard.fail_schedule_run(
            run_id=str(params["run_id"]),
            result=dict(params.get("result") or {}),
            error=str(params["error"]),
            artifact_ref=params.get("artifact_ref"),
            command_id=params.get("command_id"),
            next_run_at=params.get("next_run_at"),
        )
        await _audit(
            app,
            EventType.ONGUARD_SCHEDULE_RUN,
            {
                "schedule_id": run["schedule_id"],
                "client_id": run["client_id"],
                "run_id": run["run_id"],
                "status": run["status"],
                "error": run["error"],
            },
        )
        return {"run": run}

    async def schedule_history(params: dict[str, Any]) -> dict[str, Any]:
        return {
            "runs": await app.onguard.schedule_history(
                schedule_id=params.get("schedule_id"),
                client_id=params.get("client_id"),
                limit=int(params.get("limit", 100)),
            )
        }

    async def notifications_contract(params: dict[str, Any]) -> dict[str, Any]:
        return {
            "classes": [
                {
                    "class": "result",
                    "urgency": "normal",
                    "ttl_seconds": 86_400,
                    "requires_operator_action": False,
                    "client_behavior": "render summary and deep-link to daemon artifact/event",
                },
                {
                    "class": "approval_needed",
                    "urgency": "high",
                    "ttl_seconds": 3_600,
                    "requires_operator_action": True,
                    "client_behavior": "open approval card; never approve from notification body",
                },
                {
                    "class": "failure",
                    "urgency": "high",
                    "ttl_seconds": 86_400,
                    "requires_operator_action": False,
                    "client_behavior": "open event details and linked artifact if present",
                },
            ],
            "deep_link_scheme": "capdep://onguard/{event_id}",
            "dedupe_key": "event_id",
        }

    async def notifications_list(params: dict[str, Any]) -> dict[str, Any]:
        events = await app.onguard.list_events(
            client_id=params.get("client_id"),
            limit=int(params.get("limit", 100)),
        )
        include_acknowledged = bool(params.get("include_acknowledged", False))
        notifications = []
        seen: set[str] = set()
        for event in events:
            if event.get("acknowledged_at") and not include_acknowledged:
                continue
            key = str(event.get("event_id"))
            if key in seen:
                continue
            seen.add(key)
            notifications.append(_notification_from_event(event))
        return {
            "notifications": notifications,
            "suppressed_duplicates": max(0, len(events) - len(notifications)),
            "contract": (await notifications_contract({})),
        }

    async def approval_digest(params: dict[str, Any]) -> dict[str, Any]:
        from capabledeputy.approval.model import ApprovalStatus
        from capabledeputy.approval.strong_auth import approval_to_client_dict
        from capabledeputy.daemon.settings_store import load_settings

        settings = load_settings()
        pending = [
            approval_to_client_dict(
                approval,
                touch_id_policy_enabled=settings.require_touch_id_for_high_risk,
            )
            for approval in app.approval_queue.list(status=ApprovalStatus.PENDING)
        ]
        groups: dict[str, list[dict[str, Any]]] = {}
        for item in pending:
            group = str(item.get("sibling_group_id") or f"solo:{item.get('id')}")
            groups.setdefault(group, []).append(item)
        return {
            "groups": [
                _approval_group_digest(group_id, items)
                for group_id, items in sorted(groups.items())
            ],
            "pending_count": len(pending),
            "bulk_approval_rule": (
                "Bulk approval is available only through approval.approve_group "
                "and still evaluates each exact pending approval payload."
            ),
        }

    async def artifact_handoff(params: dict[str, Any]) -> dict[str, Any]:
        artifact = await app.onguard.read_artifact(artifact_id=str(params["artifact_id"]))
        intent = str(
            params.get("intent") or f"Review onguard artifact {artifact['artifact_id']}",
        )
        session = await app.graph.new(
            owner=str(params.get("owner", "interactive-client")),
            intent=intent,
            origin={
                "kind": "onguard_handoff",
                "client_id": artifact["client_id"],
                "command_id": artifact.get("command_id"),
                "schedule_id": artifact.get("schedule_id"),
                "metadata": {"artifact_id": artifact["artifact_id"]},
            },
        )
        if artifact.get("labels"):
            from capabledeputy.policy.labels import tags_for_labels_strings

            session = await app.graph.add_tags(
                session.id,
                tags_for_labels_strings(frozenset(str(v) for v in artifact["labels"])),
            )
        await app.graph.add_turn(
            session.id,
            Turn(
                turn_id=len(session.history),
                role="user",
                content=f"Review onguard artifact {artifact['artifact_id']}",
            ),
        )
        await app.graph.add_turn(
            session.id,
            Turn(
                turn_id=len(session.history) + 1,
                role="agent",
                content=json.dumps(
                    {
                        "artifact_id": artifact["artifact_id"],
                        "artifact_type": artifact["artifact_type"],
                        "payload": artifact["payload"],
                        "labels": artifact["labels"],
                        "provenance": artifact["provenance"],
                    },
                    indent=2,
                    sort_keys=True,
                ),
            ),
        )
        session = app.graph.get(session.id)
        await _audit(
            app,
            EventType.ONGUARD_ARTIFACT_CHANGED,
            {
                "artifact_id": artifact["artifact_id"],
                "client_id": artifact["client_id"],
                "status": artifact["status"],
                "handoff_session_id": str(session.id),
            },
        )
        return {"artifact": artifact, "session": session.to_dict()}

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
        "client.events.ack": events_ack,
        "artifact.create": artifact_create,
        "artifact.read": artifact_read,
        "artifact.list": artifact_list,
        "artifact.promote": artifact_promote,
        "artifact.delete": artifact_delete,
        "schedule.create": schedule_create,
        "schedule.list": schedule_list,
        "schedule.update": schedule_update,
        "schedule.disable": schedule_disable,
        "schedule.run_now": schedule_run_now,
        "schedule.claim_due": schedule_claim_due,
        "schedule.complete_run": schedule_complete_run,
        "schedule.fail_run": schedule_fail_run,
        "schedule.history": schedule_history,
        "onguard.notifications.contract": notifications_contract,
        "onguard.notifications.list": notifications_list,
        "onguard.approval_digest": approval_digest,
        "artifact.handoff": artifact_handoff,
    }


def _notification_from_event(event: dict[str, Any]) -> dict[str, Any]:
    event_type = str(event.get("event_type") or "")
    payload = dict(event.get("payload") or {})
    notification_class = "result"
    urgency = "normal"
    ttl_seconds = 86_400
    if "approval" in event_type or payload.get("approval_id"):
        notification_class = "approval_needed"
        urgency = "high"
        ttl_seconds = 3_600
    elif "fail" in event_type or "error" in payload:
        notification_class = "failure"
        urgency = "high"
    title = str(payload.get("title") or event_type.replace(".", " ").title())
    body = str(payload.get("summary") or payload.get("reason") or payload.get("error") or "")
    return {
        "id": event["event_id"],
        "class": notification_class,
        "urgency": urgency,
        "ttl_seconds": ttl_seconds,
        "title": title,
        "body": body,
        "client_id": event["client_id"],
        "event_type": event_type,
        "labels": event.get("labels", []),
        "deep_link": f"capdep://onguard/{event['event_id']}",
        "artifact_ref": payload.get("artifact_ref"),
        "approval_id": payload.get("approval_id"),
        "acknowledged_at": event.get("acknowledged_at"),
        "created_at": event.get("created_at"),
    }


def _approval_group_digest(group_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    action_set = sorted({str(item.get("action") or "") for item in items})
    return {
        "group_id": group_id,
        "count": len(items),
        "actions": action_set,
        "requires_strong_auth": any(bool(item.get("requires_strong_auth")) for item in items),
        "items": [
            {
                "id": item.get("id"),
                "action": item.get("action"),
                "target": item.get("target"),
                "payload_sha256": hashlib.sha256(
                    str(item.get("payload") or "").encode("utf-8"),
                ).hexdigest(),
                "labels_in": item.get("labels_in", []),
                "labels_out": item.get("labels_out", []),
                "expires_at": item.get("expires_at"),
            }
            for item in items
        ],
    }
