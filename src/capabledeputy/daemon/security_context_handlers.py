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
        provenance = _provenance(
            scoped_events,
            offset=_bounded_nonnegative(params.get("provenance_offset"), default=0),
            limit=_bounded_limit(params.get("provenance_limit"), default=100, maximum=500),
            kind_filter=str(params.get("provenance_kind") or "").strip() or None,
        )
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
                # This is a content-free explanation of how labels reached
                # the session.  Clients render daemon audit evidence rather
                # than infer authority or taint from planner text.
                "propagation": _label_propagation(scoped_events),
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
                "tool_surface": _tool_surface(scoped_events),
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


def _bounded_nonnegative(value: Any, *, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _bounded_limit(value: Any, *, default: int, maximum: int) -> int:
    try:
        return min(maximum, max(1, int(value)))
    except (TypeError, ValueError):
        return default


def _provenance(
    events: list[Event],
    *,
    offset: int,
    limit: int,
    kind_filter: str | None,
) -> dict[str, Any]:
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
    total_edges = len(edges)
    all_nodes = sorted(nodes.values(), key=lambda n: n["id"])
    if kind_filter:
        all_nodes = [node for node in all_nodes if node["kind"] == kind_filter]
        visible_node_ids = {node["id"] for node in all_nodes}
        edges = [
            edge
            for edge in edges
            if edge["from"] in visible_node_ids or edge["to"] in visible_node_ids
        ]
    page_nodes = all_nodes[offset : offset + limit]
    page_edges = edges[offset : offset + limit]
    return {
        "nodes": page_nodes,
        "edges": page_edges,
        "node_count": len(nodes),
        "edge_count": total_edges,
        "filtered_edge_count": len(edges),
        "node_offset": offset,
        "node_limit": limit,
        "node_returned": len(page_nodes),
        "node_has_more": offset + len(page_nodes) < len(all_nodes),
        "edge_offset": offset,
        "edge_limit": limit,
        "edge_returned": len(page_edges),
        "edge_has_more": offset + len(page_edges) < len(edges),
        "kind_filter": kind_filter,
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
        "approval_gate_count": sum(1 for d in decisions if d.get("decision") == "require_approval"),
        "matched_rule_ids": sorted(
            {str(rule) for d in decisions for rule in d.get("v2_matched_rule_ids", []) if rule},
        ),
    }


def _label_propagation(events: list[Event]) -> list[dict[str, Any]]:
    """Project label-ingress evidence without copying any tool output.

    A propagation record says which daemon-dispatched tool contributed the
    category/provenance labels.  It intentionally exposes neither arguments
    nor returned content, preserving the separation between inspectable audit
    control data and labeled session/reference data.
    """
    propagated = []
    for event in events:
        if event.event_type != EventType.LABEL_PROPAGATED:
            continue
        payload = event.payload
        propagated.append(
            {
                "audit_id": str(event.audit_id),
                "timestamp": event.timestamp.isoformat(),
                "source_tool": str(payload.get("source_tool") or ""),
                "categories_added": [str(value) for value in payload.get("categories_added") or []],
                "levels_added": [str(value) for value in payload.get("levels_added") or []],
            }
        )
    return propagated[-50:]


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
    server_status = getattr(manager, "server_status", {}) or {}
    for status in server_status.values():
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


def _tool_surface(events: list[Event]) -> dict[str, Any]:
    """Project planner-visible tool selections as read-only session evidence.

    The planner picks from a daemon-owned filtered surface.  Rendering this
    audit projection lets an operator distinguish tools the planner could see
    from tools it actually used, without allowing a client to widen that
    surface or to manufacture a selection record.
    """
    selections = []
    for event in events:
        if event.event_type != EventType.TOOL_SURFACE_SELECTED:
            continue
        payload = event.payload
        selections.append(
            {
                "audit_id": str(event.audit_id),
                "timestamp": event.timestamp.isoformat(),
                "method": payload.get("method"),
                "n_visible": payload.get("n_visible"),
                "n_selected": payload.get("n_selected"),
                "selected": list(payload.get("selected") or []),
                "mandatory_added": list(payload.get("mandatory_added") or []),
                "selection_reason": payload.get("selection_reason"),
            },
        )
    return {"selection_count": len(selections), "recent_selections": selections[-20:]}


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
