"""Daemon-owned security-context projection for sessions.

Clients should not reconstruct security posture by stitching together
session, audit, approval, provenance, onguard, and upstream-MCP RPCs. This
handler materializes one stable read model from daemon state so every client
renders the same security facts and the daemon remains the source of truth.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from capabledeputy.app import App
from capabledeputy.audit.events import Event, EventType
from capabledeputy.daemon.handlers import Handler
from capabledeputy.policy.labels import legacy_labels_present


def make_security_context_handlers(app: App) -> dict[str, Handler]:
    async def session_security_context(params: dict[str, Any]) -> dict[str, Any]:
        session_id = UUID(str(params["session_id"]))
        session = app.graph.get(session_id)
        session_dict = session.to_dict()
        events = await app.audit.read_all()
        scoped_events = [e for e in events if _event_mentions_session(e, str(session_id))]
        approvals = [
            a.to_dict()
            for a in app.approval_queue.list(status=None)
            if str(a.from_session) == str(session_id) or str(a.to_session) == str(session_id)
        ]
        provenance = _provenance(scoped_events)
        policy = _policy(scoped_events)
        origin = session.origin.to_dict()
        onguard = await _onguard(app, origin)
        external_actors = _external_actors(app, scoped_events, origin)
        label_set = legacy_labels_present(session.label_state)

        return {
            "schema_version": 1,
            "session": {
                "id": str(session.id),
                "parent": str(session.parent) if session.parent else None,
                "status": session.status.value,
                "owner": session.owner,
                "intent": session.intent,
                "purpose_handle": session.purpose_handle,
                "enforcement_mode": session.enforcement_mode.value,
                "first_use_prompt_enabled": session.first_use_prompt_enabled,
                "prefer_programmatic": session.prefer_programmatic,
                "tool_aliasing": session.tool_aliasing,
                "history_turn_count": len(session.history),
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
            },
            "labels": {
                "label_state": session.label_state.to_dict(),
                "axis_d": session.axis_d.to_dict(),
                "legacy_label_set": label_set,
            },
            "capabilities": {
                "active": session_dict["capability_set"],
                "used_kinds": session_dict["used_kinds"],
                "revoked_audit_ids": session_dict["revoked_audit_ids"],
                "cap_uses": session_dict["cap_uses"],
            },
            "origin": origin,
            "actors": {
                "session_origin": origin,
                "external_mcp": external_actors["mcp"],
                "tools": external_actors["tools"],
                "onguard": onguard,
            },
            "approvals": {
                "requests": approvals,
                "pending_count": sum(1 for a in approvals if a.get("status") == "pending"),
                "expired_count": sum(1 for a in approvals if a.get("status") == "expired"),
            },
            "policy": policy,
            "provenance": provenance,
            "security_models": _security_models(
                session_dict=session_dict,
                label_set=label_set,
                approvals=approvals,
                policy=policy,
                provenance=provenance,
                onguard=onguard,
            ),
            "flow_patterns": _flow_patterns(session_dict, policy, provenance, approvals),
            "audit_evidence": [_event_summary(e) for e in scoped_events[-100:]],
            "limitations": _limitations(scoped_events, provenance, external_actors),
        }

    return {"session.security_context": session_security_context}


def _event_mentions_session(event: Event, session_id: str) -> bool:
    if event.session_id and str(event.session_id) == session_id:
        return True
    payload = event.payload
    for key in (
        "session_id",
        "from_session",
        "to_session",
        "parent_session_id",
        "child_session_id",
    ):
        if str(payload.get(key) or "") == session_id:
            return True
    return False


def _event_summary(event: Event) -> dict[str, Any]:
    return {
        "audit_id": str(event.audit_id),
        "timestamp": event.timestamp.isoformat(),
        "event_type": event.event_type.value,
        "session_id": str(event.session_id) if event.session_id else None,
        "payload": event.payload,
    }


def _provenance(events: list[Event]) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    for event in events:
        payload = event.payload
        if event.event_type == EventType.PROVENANCE_NODE:
            node_id = str(payload.get("node_id") or "")
            if node_id:
                nodes[node_id] = {
                    "id": node_id,
                    "kind": payload.get("kind", ""),
                    "materialized_id": payload.get("materialized_id", ""),
                    "label_state": payload.get("label_state"),
                    "metadata": payload.get("metadata") or {},
                }
        elif event.event_type == EventType.PROVENANCE_EDGE:
            edges.append(
                {
                    "from": payload.get("from_node_id", ""),
                    "to": payload.get("to_node_id", ""),
                    "kind": payload.get("kind", ""),
                    "metadata": payload.get("metadata") or {},
                },
            )
    return {
        "nodes": sorted(nodes.values(), key=lambda n: n["id"]),
        "edges": edges,
        "node_count": len(nodes),
        "edge_count": len(edges),
    }


def _policy(events: list[Event]) -> dict[str, Any]:
    decisions = []
    shadows = []
    inspectors = []
    for event in events:
        payload = event.payload
        if event.event_type == EventType.POLICY_DECIDED:
            decisions.append(
                {
                    "audit_id": str(event.audit_id),
                    "timestamp": event.timestamp.isoformat(),
                    "tool": payload.get("tool"),
                    "decision": payload.get("decision"),
                    "rule": payload.get("rule"),
                    "reason": payload.get("reason"),
                    "v2_outcome": payload.get("v2_outcome"),
                    "v2_matched_rule_ids": payload.get("v2_matched_rule_ids", []),
                    "effect_class": payload.get("effect_class"),
                },
            )
        elif event.event_type == EventType.POLICY_SHADOWED:
            shadows.append(_event_summary(event))
        elif event.event_type in {
            EventType.INSPECTOR_APPLIED,
            EventType.DECISION_INSPECTOR_APPLIED,
            EventType.DECLASSIFIER_APPLIED,
        }:
            inspectors.append(_event_summary(event))
    return {
        "recent_decisions": decisions[-50:],
        "shadowed_decisions": shadows[-50:],
        "programmatic_transforms": inspectors[-50:],
        "decision_count": len(decisions),
        "deny_count": sum(1 for d in decisions if d.get("decision") == "deny"),
        "approval_gate_count": sum(
            1 for d in decisions if d.get("decision") == "require_approval"
        ),
        "matched_rule_ids": sorted(
            {
                str(rule)
                for d in decisions
                for rule in d.get("v2_matched_rule_ids", [])
                if rule
            },
        ),
    }


async def _onguard(app: App, origin: dict[str, Any]) -> dict[str, Any]:
    client_id = origin.get("client_id")
    command_id = origin.get("command_id")
    schedule_id = origin.get("schedule_id")
    if not client_id and not command_id and not schedule_id:
        return {
            "client": None,
            "commands": [],
            "events": [],
            "schedules": [],
            "artifacts": [],
        }
    commands = await app.onguard.list_commands(client_id=client_id)
    if command_id:
        commands = [c for c in commands if c.get("command_id") == command_id]
    schedules = await app.onguard.list_schedules(client_id=client_id)
    if schedule_id:
        schedules = [s for s in schedules if s.get("schedule_id") == schedule_id]
    clients = await app.onguard.list_clients(kind=None)
    client = next((c for c in clients if c.get("client_id") == client_id), None)
    return {
        "client": client,
        "commands": commands,
        "events": await app.onguard.list_events(client_id=client_id, limit=50) if client_id else [],
        "schedules": schedules,
        "artifacts": await app.onguard.list_artifacts(client_id=client_id) if client_id else [],
    }


def _external_actors(
    app: App,
    events: list[Event],
    origin: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    mcp: dict[str, dict[str, Any]] = {}
    manager = getattr(app, "upstream_manager", None)
    for status in getattr(manager, "server_status", []) or []:
        mcp[str(status.name)] = {
            "name": status.name,
            "state": status.state,
            "transport": status.transport,
            "registered_tool_count": status.registered_tool_count,
            "rejected_tool_count": status.rejected_tool_count,
        }
    origin_kind = str(origin.get("kind") or "")
    if "mcp" in origin_kind:
        name = str(origin.get("client_id") or origin_kind)
        mcp.setdefault(name, {"name": name, "state": "origin", "transport": None})

    tools: dict[str, dict[str, Any]] = {}
    for event in events:
        payload = event.payload
        tool = payload.get("tool")
        if tool:
            name = str(tool)
            tools[name] = {
                "name": name,
                "effect_class": payload.get("effect_class"),
                "last_event_type": event.event_type.value,
            }
    return {
        "mcp": sorted(mcp.values(), key=lambda a: str(a.get("name"))),
        "tools": sorted(tools.values(), key=lambda a: str(a.get("name"))),
    }


def _security_models(
    *,
    session_dict: dict[str, Any],
    label_set: list[str],
    approvals: list[dict[str, Any]],
    policy: dict[str, Any],
    provenance: dict[str, Any],
    onguard: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        {
            "name": "object_capability_reference_monitor",
            "implemented": True,
            "evidence": {
                "capability_count": len(session_dict["capability_set"]),
                "used_kinds": session_dict["used_kinds"],
                "decision_count": policy["decision_count"],
            },
        },
        {
            "name": "information_flow_labels",
            "implemented": True,
            "evidence": {
                "label_set": label_set,
                "label_state": session_dict["label_state"],
            },
        },
        {
            "name": "approval_declassification",
            "implemented": True,
            "evidence": {
                "approval_count": len(approvals),
                "pending_count": sum(1 for a in approvals if a.get("status") == "pending"),
                "expired_count": sum(1 for a in approvals if a.get("status") == "expired"),
            },
        },
        {
            "name": "materialized_provenance_dag",
            "implemented": provenance["node_count"] > 0 or provenance["edge_count"] > 0,
            "evidence": {
                "node_count": provenance["node_count"],
                "edge_count": provenance["edge_count"],
            },
        },
        {
            "name": "headless_client_origin_controls",
            "implemented": bool(onguard["client"] or onguard["commands"] or onguard["schedules"]),
            "evidence": {
                "client": onguard["client"],
                "command_count": len(onguard["commands"]),
                "schedule_count": len(onguard["schedules"]),
            },
        },
    ]


def _flow_patterns(
    session_dict: dict[str, Any],
    policy: dict[str, Any],
    provenance: dict[str, Any],
    approvals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "name": "turn_level_policy_chokepoint",
            "active": policy["decision_count"] > 0,
            "evidence": {"decision_count": policy["decision_count"]},
        },
        {
            "name": "dual_llm_quarantine_or_extract",
            "active": any(t.get("kind") == "declassifier" for t in provenance["nodes"]),
            "evidence": {"provenance_node_count": provenance["node_count"]},
        },
        {
            "name": "reference_handle_restricted_data",
            "active": bool(session_dict.get("reference_handles")),
            "evidence": {
                "reference_handle_count": len(session_dict.get("reference_handles") or {}),
            },
        },
        {
            "name": "sandbox_or_isolated_actuation",
            "active": any(
                d.get("effect_class") in {"sandbox", "devbox"} for d in policy["recent_decisions"]
            ),
            "evidence": {"recent_tools": [d.get("tool") for d in policy["recent_decisions"][-10:]]},
        },
        {
            "name": "human_approval_gate",
            "active": bool(approvals),
            "evidence": {"approval_count": len(approvals)},
        },
        {
            "name": "shadow_policy_rollout",
            "active": session_dict.get("enforcement_mode") == "shadow"
            or bool(policy["shadowed_decisions"]),
            "evidence": {"shadowed_decision_count": len(policy["shadowed_decisions"])},
        },
    ]


def _limitations(
    events: list[Event],
    provenance: dict[str, Any],
    external_actors: dict[str, list[dict[str, Any]]],
) -> list[str]:
    out: list[str] = []
    if not events:
        out.append("No session-scoped audit evidence has been written yet.")
    if provenance["node_count"] == 0 and provenance["edge_count"] == 0:
        out.append("No materialized provenance DAG entries exist for this session yet.")
    if not external_actors["mcp"]:
        out.append("No upstream MCP actor evidence is associated with this session.")
    return out
