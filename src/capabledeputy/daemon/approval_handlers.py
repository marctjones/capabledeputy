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
from capabledeputy.policy.labels import (
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
)


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
        from capabledeputy.policy.labels import tags_for_labels_strings

        labels_in_flat = frozenset(s for s in params.get("labels_in", []))
        labels_out_flat = frozenset(s for s in params.get("labels_out", []))
        request = await app.approval_queue.submit(
            from_session=UUID(params["from_session"]),
            action=ApprovalAction(params["action"]),
            payload=str(params["payload"]),
            target=str(params["target"]),
            labels_in=tags_for_labels_strings(labels_in_flat),
            labels_out=tags_for_labels_strings(labels_out_flat),
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

        if approved.action == ApprovalAction.QUEUE_PURCHASE:
            new_session, dispatch_outcome = await _execute_declassified_purchase(
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

        if approved.action == ApprovalAction.EXECUTE_DESTRUCTIVE:
            new_session, dispatch_outcome = await _execute_declassified_destructive(
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
                    "reason": dispatch_outcome.reason,
                },
            }

        return {"approval": approved.to_dict()}

    async def approval_approve_group(params: dict[str, Any]) -> dict[str, Any]:
        """Approve every PENDING sibling in `group_id`. Each approved
        sibling runs the same per-action dispatch logic as a solo
        approve (send → declassified send-mail session, purchase →
        declassified queue, destructive → declassified destructive
        path). Already-decided siblings are skipped — the operator
        may have denied one individually before clicking approve-all.

        Returns a list of per-sibling results in id order. Errors on
        any single sibling don't stop the group; the failing one
        ends up with `error` set in its result dict."""
        group_id = UUID(params["group_id"])
        decided_by = str(params.get("decided_by", "user"))
        members = app.approval_queue.siblings(group_id)
        results: list[dict[str, Any]] = []
        for m in members:
            if m.status != ApprovalStatus.PENDING:
                # Skip already-decided siblings; surface their state
                # so the operator can see why this one didn't go
                # through the approve path.
                results.append(
                    {
                        "id": m.id,
                        "skipped": True,
                        "reason": f"status={m.status.value}",
                        "approval": m.to_dict(),
                    },
                )
                continue
            try:
                # Route per-id through the regular approve handler so
                # the per-action dispatch behavior is identical (no
                # second copy of the send/purchase/destructive logic).
                result = await approval_approve(
                    {"id": m.id, "decided_by": decided_by},
                )
                result["id"] = m.id
                results.append(result)
            except Exception as e:
                results.append(
                    {
                        "id": m.id,
                        "error": str(e),
                    },
                )
        return {
            "group_id": str(group_id),
            "results": results,
            "n_total": len(members),
            "n_approved": sum(1 for r in results if not r.get("skipped") and not r.get("error")),
            "n_skipped": sum(1 for r in results if r.get("skipped")),
            "n_failed": sum(1 for r in results if r.get("error")),
        }

    return {
        "approval.list": approval_list,
        "approval.show": approval_show,
        "approval.submit": approval_submit,
        "approval.approve": approval_approve,
        "approval.approve_group": approval_approve_group,
        "approval.deny": approval_deny,
        "approval.defer": approval_defer,
    }


async def _execute_declassified_destructive(
    app: App,
    payload: str,
    target: str,
    origin_session: UUID,
) -> tuple[UUID, Any]:
    """Execute an approved destructive-op (MODIFY_* or DELETE_* of an
    existing resource) via a TRUSTED_USER_DIRECT purpose session with
    a one-shot `allows_destructive=True` capability scoped exactly to
    the action's kind and target.

    Payload is a JSON dict: {"tool": "memory.update", "args": {...}}.
    The CapabilityKind to grant is derived from the tool definition
    in the registry — no kind tunneling through the payload.
    """
    import json as _json

    try:
        decoded = _json.loads(payload)
        tool_name = str(decoded["tool"])
        tool_args = dict(decoded.get("args", {}))
    except (_json.JSONDecodeError, KeyError, ValueError) as e:
        from capabledeputy.policy.rules import Decision
        from capabledeputy.tools.client import ToolCallOutcome

        return UUID(int=0), ToolCallOutcome(
            decision=Decision.DENY,
            reason=f"malformed destructive-op payload: {e}",
        )

    tool = app.registry.get(tool_name)
    cap = Capability(
        kind=tool.capability_kind,
        pattern=target,
        expiry=CapabilityExpiry.ONE_SHOT,
        origin=CapabilityOrigin.USER_APPROVED,
        allows_destructive=True,
    )
    purpose_session = await app.graph.new(
        intent=(
            f"declassified destructive {tool_name} on {target} (approved from {origin_session})"
        ),
    )
    granted = await app.graph.grant_capability(purpose_session.id, cap)
    # Seed with TRUSTED_USER_DIRECT provenance via add_tags
    await app.graph.add_tags(
        granted.id,
        LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.PRINCIPAL_DIRECT)})),
    )

    outcome = await app.tool_client.call_tool(granted.id, tool_name, tool_args)
    await app.graph.abort(granted.id)
    return granted.id, outcome


async def _execute_declassified_purchase(
    app: App,
    payload: str,
    target: str,
    origin_session: UUID,
) -> tuple[UUID, Any]:
    """Approval payload for QUEUE_PURCHASE is a JSON dict of the original
    args (item, amount, etc.). We spawn a TRUSTED_USER_DIRECT purpose
    session with a one-shot QUEUE_PURCHASE cap scoped to the vendor and
    dispatch via the standard tool client."""
    import json as _json

    try:
        args = _json.loads(payload)
    except _json.JSONDecodeError:
        args = {"item": payload}
    amount = args.get("amount")

    cap = Capability(
        kind=CapabilityKind.QUEUE_PURCHASE,
        pattern=target,
        expiry=CapabilityExpiry.ONE_SHOT,
        origin=CapabilityOrigin.USER_APPROVED,
        max_amount=int(amount) if isinstance(amount, int) else None,
    )
    purpose_session = await app.graph.new(
        intent=f"declassified purchase at {target} (approved from {origin_session})",
    )
    granted = await app.graph.grant_capability(purpose_session.id, cap)
    # Seed with TRUSTED_USER_DIRECT provenance via add_tags
    await app.graph.add_tags(
        granted.id,
        LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.PRINCIPAL_DIRECT)})),
    )

    call_args = {"vendor": target, **{k: v for k, v in args.items() if k != "vendor"}}
    outcome = await app.tool_client.call_tool(granted.id, "purchase.queue", call_args)
    await app.graph.abort(granted.id)
    return granted.id, outcome


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
    # Seed with TRUSTED_USER_DIRECT provenance via add_tags
    await app.graph.add_tags(
        granted.id,
        LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.PRINCIPAL_DIRECT)})),
    )

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
