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
from capabledeputy.policy.labels import Label
from capabledeputy.session.graph import SessionGraph
from capabledeputy.session.model import SessionStatus


def make_session_handlers(graph: SessionGraph) -> dict[str, Handler]:
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
            tool_aliasing=bool(params.get("tool_aliasing", False)),
            prefer_programmatic=bool(params.get("prefer_programmatic", False)),
            parent=parent,
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
        s = await graph.abort(UUID(params["session_id"]))
        return s.to_dict()

    async def session_get(params: dict[str, Any]) -> dict[str, Any]:
        s = graph.get(UUID(params["session_id"]))
        return s.to_dict()

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
        labels = frozenset(Label(s) for s in params.get("labels", []))
        s = await graph.add_labels(UUID(params["session_id"]), labels)
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
        "session.children": session_children,
        "session.add_labels": session_add_labels,
        "session.set_enforcement": session_set_enforcement,
        "session.set_first_use_prompts": session_set_first_use_prompts,
        "session.delegate": session_delegate,
        "capability.revoke": capability_revoke,
    }
