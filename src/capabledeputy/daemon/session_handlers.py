"""RPC handlers for session lifecycle (DESIGN.md §6)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from capabledeputy.daemon.handlers import Handler
from capabledeputy.policy.capabilities import (
    CapabilityExpiry,
    CapabilityKind,
    DelegationRefusal,
    DelegationRequest,
    RateLimit,
)
from capabledeputy.session.coordination import WorkstreamOwnershipError
from capabledeputy.session.graph import SessionGraph
from capabledeputy.session.model import SessionStatus


def make_session_handlers(
    graph: SessionGraph,
    coordinator: Any = None,
    workstreams: Any = None,
) -> dict[str, Handler]:
    async def session_list(params: dict[str, Any]) -> dict[str, Any]:
        status_str = params.get("status")
        status = SessionStatus(status_str) if status_str else None
        sessions = graph.list(status=status)
        return {"sessions": [s.to_dict() for s in sessions]}

    async def session_new(params: dict[str, Any]) -> dict[str, Any]:
        parent_str = params.get("parent")
        parent = UUID(parent_str) if parent_str else None
        s = await graph.new(
            owner=params.get("owner"),
            intent=params.get("intent"),
            purpose_handle=str(params.get("purpose_handle", "unset")),
            tool_aliasing=bool(params.get("tool_aliasing", False)),
            prefer_programmatic=bool(params.get("prefer_programmatic", False)),
            parent=parent,
            origin=params.get("origin"),
        )
        return s.to_dict()

    async def session_fork(params: dict[str, Any]) -> dict[str, Any]:
        parent_id = UUID(params["parent_id"])
        s = await graph.fork(parent_id, intent=params.get("intent"))
        return s.to_dict()

    async def session_pause(params: dict[str, Any]) -> dict[str, Any]:
        s = await graph.pause(UUID(params["session_id"]))
        return s.to_dict()

    async def session_resume(params: dict[str, Any]) -> dict[str, Any]:
        s = await graph.resume(UUID(params["session_id"]))
        return s.to_dict()

    async def session_abort(params: dict[str, Any]) -> dict[str, Any]:
        if workstreams is not None:
            await workstreams.release_session(UUID(params["session_id"]), reason="session aborted")
        s = await graph.abort(UUID(params["session_id"]))
        return s.to_dict()

    async def session_get(params: dict[str, Any]) -> dict[str, Any]:
        s = graph.get(UUID(params["session_id"]))
        return s.to_dict()

    async def session_events(params: dict[str, Any]) -> dict[str, Any]:
        if coordinator is None:
            return {"events": [], "next_cursor": int(params.get("cursor") or 0)}
        session_id = UUID(params["session_id"]) if params.get("session_id") else None
        return coordinator.events_since(
            session_id=session_id,
            cursor=int(params.get("cursor") or 0),
            limit=int(params.get("limit") or 200),
        )

    async def session_input_submit(params: dict[str, Any]) -> dict[str, Any]:
        if coordinator is None:
            raise RuntimeError("session coordinator unavailable")
        session_id = UUID(params["session_id"])
        workstream = None
        if workstreams is not None:
            workstream = await _claim_or_verify_workstream(
                session_id,
                params,
                workstreams,
            )
        item = coordinator.enqueue_input(
            session_id,
            str(params["message"]),
            submitted_by=str(
                params.get("submitted_by")
                or params.get("client_id")
                or "client",
            ),
        )
        await coordinator.emit(
            session_id,
            "input_queued",
            {"input": item.to_dict(), "pending_count": len(coordinator.pending_inputs(session_id))},
        )
        result = {"queued": True, "input": item.to_dict()}
        if workstream is not None:
            result["workstream"] = workstream.to_dict(include_token=False)
        return result

    async def session_input_queue(params: dict[str, Any]) -> dict[str, Any]:
        if coordinator is None:
            return {"inputs": []}
        return {"inputs": coordinator.pending_inputs(UUID(params["session_id"]))}

    async def session_children(params: dict[str, Any]) -> dict[str, Any]:
        children = graph.children(UUID(params["session_id"]))
        return {"sessions": [s.to_dict() for s in children]}

    async def session_set_first_use_prompts(
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Cookbook §4 #6 — flip first-action-of-kind prompts.
        Operator-only (chat REPL /first-use or direct CLI)."""
        s = await graph.set_first_use_prompts(
            UUID(params["session_id"]),
            bool(params["enabled"]),
        )
        return s.to_dict()

    async def session_set_enforcement(params: dict[str, Any]) -> dict[str, Any]:
        """Pattern ⑥ — flip the session's enforcement posture.
        Operator-only (chat REPL /enforce or direct CLI). The chat
        REPL handles the affordance; the AI cannot self-shadow."""
        from capabledeputy.session.model import EnforcementMode

        s = await graph.set_enforcement_mode(
            UUID(params["session_id"]),
            EnforcementMode(str(params["mode"])),
        )
        return s.to_dict()

    async def session_add_labels(params: dict[str, Any]) -> dict[str, Any]:
        from capabledeputy.policy.labels import tags_for_labels_strings

        labels_strs = frozenset(s for s in params.get("labels", []))
        # Convert legacy label strings to LabelState
        label_state = tags_for_labels_strings(labels_strs)
        s = await graph.add_tags(UUID(params["session_id"]), label_state)
        return s.to_dict()

    async def capability_revoke(params: dict[str, Any]) -> dict[str, Any]:
        """002 US2 — revoke a capability by audit_id within a session.

        Adds the audit_id to the session's revoked_audit_ids set.
        Cascade computed lazily at next decide(); any descendant
        across the spawn graph that traces back to this ancestor is
        denied at that point with capability-cascaded. Operator-only;
        the AI cannot invoke this.
        """
        session_id = UUID(params["session_id"])
        audit_id = UUID(params["audit_id"])
        trigger = str(params.get("trigger", "operator-revoke"))
        s = await graph.revoke_capability(
            session_id,
            audit_id,
            trigger=trigger,
        )
        return s.to_dict()

    async def session_delegate(params: dict[str, Any]) -> dict[str, Any]:
        # Lazy import breaks the lifecycle<->handlers cycle.
        from capabledeputy.daemon.lifecycle import max_delegation_depth

        rl = params.get("rate_limit")
        request = DelegationRequest(
            kind=CapabilityKind(params["kind"]),
            pattern=params.get("pattern"),
            max_amount=params.get("max_amount"),
            expires_at=(
                datetime.fromisoformat(params["expires_at"]) if params.get("expires_at") else None
            ),
            rate_limit=(RateLimit.from_dict(rl) if rl else None),
            expiry=(CapabilityExpiry(params["expiry"]) if params.get("expiry") else None),
            add_revoked_by=frozenset(CapabilityKind(k) for k in params.get("add_revoked_by", ())),
        )
        result = await graph.delegate(
            UUID(params["parent_session_id"]),
            UUID(params["child_session_id"]),
            request,
            depth_limit=max_delegation_depth(),
        )
        if isinstance(result, DelegationRefusal):
            return {"granted": False, "reason": result.reason.value}
        return {"granted": True, "capability": result.to_dict()}

    return {
        "session.list": session_list,
        "session.new": session_new,
        "session.fork": session_fork,
        "session.pause": session_pause,
        "session.resume": session_resume,
        "session.abort": session_abort,
        "session.get": session_get,
        "session.events": session_events,
        "session.input.submit": session_input_submit,
        "session.input.queue": session_input_queue,
        "session.children": session_children,
        "session.add_labels": session_add_labels,
        "session.set_enforcement": session_set_enforcement,
        "session.set_first_use_prompts": session_set_first_use_prompts,
        "session.delegate": session_delegate,
        "capability.revoke": capability_revoke,
    }


async def _claim_or_verify_workstream(
    session_id: UUID,
    params: dict[str, Any],
    workstreams: Any,
) -> Any:
    client_id = str(params.get("client_id") or "interactive-client")
    try:
        workstream_id = params.get("workstream_id")
        if workstream_id is not None:
            return await workstreams.claim(
                session_id,
                client_id,
                lease_seconds=int(params.get("lease_seconds") or 300),
                lease_token=params.get("lease_token"),
                reason=str(params.get("reason") or "interactive session activity"),
                workstream_id=str(workstream_id),
            )
        return await workstreams.ensure(
            session_id,
            client_id,
            lease_seconds=int(params.get("lease_seconds") or 300),
            lease_token=params.get("lease_token"),
            reason=str(params.get("reason") or "interactive session activity"),
            auto_claim=bool(params.get("claim_if_missing", True)),
        )
    except WorkstreamOwnershipError as e:
        raise RuntimeError(str(e)) from e
