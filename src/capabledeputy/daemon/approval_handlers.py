"""RPC handlers for the approval queue (DESIGN.md §8).

approval.approve is where cross-session declassification happens: when
a SEND_EMAIL approval is approved, the system spawns a fresh session C
with a one-shot capability scoped exactly to the approved payload and
recipient, executes the email send in C, and returns. The originating
session never gains the egress capability — it can't, because its
labels would still conflict.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from uuid import UUID

from capabledeputy.app import App
from capabledeputy.approval.model import ApprovalAction, ApprovalStatus
from capabledeputy.daemon.handlers import Handler
from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityExpiry,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.labels import Label


def make_approval_handlers(app: App) -> dict[str, Handler]:
    async def approval_list(params: dict[str, Any]) -> dict[str, Any]:
        status = params.get("status")
        status_enum = ApprovalStatus(status) if status else None
        requests = app.approval_queue.list(status=status_enum)
        return {"approvals": [r.to_dict() for r in requests]}

    async def approval_show(params: dict[str, Any]) -> dict[str, Any]:
        request = app.approval_queue.get(int(params["id"]))
        return request.to_dict()

    async def approval_submit(params: dict[str, Any]) -> dict[str, Any]:
        request = await app.approval_queue.submit(
            from_session=UUID(params["from_session"]),
            action=ApprovalAction(params["action"]),
            payload=str(params["payload"]),
            target=str(params["target"]),
            labels_in=frozenset(Label(s) for s in params.get("labels_in", [])),
            labels_out=frozenset(Label(s) for s in params.get("labels_out", [])),
            justification=str(params.get("justification", "")),
        )
        return request.to_dict()

    async def approval_deny(params: dict[str, Any]) -> dict[str, Any]:
        request = await app.approval_queue.deny(
            int(params["id"]),
            decided_by=str(params.get("decided_by", "user")),
            reason=str(params.get("reason", "")),
        )
        return request.to_dict()

    async def approval_defer(params: dict[str, Any]) -> dict[str, Any]:
        request = await app.approval_queue.defer(int(params["id"]))
        return request.to_dict()

    async def approval_approve(params: dict[str, Any]) -> dict[str, Any]:
        request = app.approval_queue.get(int(params["id"]))
        decided_by = str(params.get("decided_by", "user"))

        approved = await app.approval_queue.approve(request.id, decided_by=decided_by)

        if approved.action == ApprovalAction.SEND_EMAIL:
            new_session, dispatch_outcome = await _execute_declassified_email(
                app,
                approved.payload,
                approved.target,
                origin_session=approved.from_session,
            )
            updated = replace(approved, to_session=new_session)
            app.approval_queue._requests[approved.id] = updated
            return {
                "approval": updated.to_dict(),
                "executed_in_session": str(new_session),
                "dispatch": {
                    "decision": dispatch_outcome.decision.value,
                    "output": dispatch_outcome.output,
                    "error": dispatch_outcome.error,
                },
            }

        return {"approval": approved.to_dict()}

    return {
        "approval.list": approval_list,
        "approval.show": approval_show,
        "approval.submit": approval_submit,
        "approval.approve": approval_approve,
        "approval.deny": approval_deny,
        "approval.defer": approval_defer,
    }


async def _execute_declassified_email(
    app: App,
    payload: str,
    target: str,
    origin_session: UUID,
) -> tuple[UUID, Any]:
    cap = Capability(
        kind=CapabilityKind.SEND_EMAIL,
        pattern=target,
        expiry=CapabilityExpiry.ONE_SHOT,
        origin=CapabilityOrigin.USER_APPROVED,
    )
    purpose_session = await app.graph.new(
        intent=f"declassified send to {target} (approved from {origin_session})",
    )
    granted = await app.graph.grant_capability(purpose_session.id, cap)
    granted = replace(
        granted,
        label_set=frozenset({Label.TRUSTED_USER_DIRECT}),
    )
    app.graph._sessions[granted.id] = granted

    outcome = await app.tool_client.call_tool(
        granted.id,
        "email.send",
        {
            "to": target,
            "subject": "Approved declassified message",
            "body": payload,
        },
    )

    await app.graph.abort(granted.id)
    return granted.id, outcome
