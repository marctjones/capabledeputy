"""RPC handlers for the approval queue (DESIGN.md §8).

approval.approve is where cross-session declassification happens: when
a SEND_EMAIL approval is approved, the system spawns a fresh session C
with a one-shot capability scoped exactly to the approved payload and
recipient, executes the email send in C, and returns. The originating
session never gains the egress capability — it can't, because its
labels would still conflict.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any
from uuid import UUID

from capabledeputy.app import App
from capabledeputy.approval.model import ApprovalAction, ApprovalStatus
from capabledeputy.approval.strong_auth import (
    approval_requires_strong_auth,
    approval_to_client_dict,
    authenticate_approval,
)
from capabledeputy.daemon.handlers import Handler
from capabledeputy.daemon.settings_store import load_settings
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
    label_join,
)
from capabledeputy.policy.tiers import Tier, is_above
from capabledeputy.provenance import ProvenanceRecorder, approval_request_node_id, stable_digest
from capabledeputy.tools.native.email import single_recipient
from capabledeputy.tools.native.email_delivery import smtp_config_fingerprint


def make_approval_handlers(app: App) -> dict[str, Handler]:
    decision_locks: dict[int, asyncio.Lock] = {}

    async def approval_list(params: dict[str, Any]) -> dict[str, Any]:
        status = params.get("status")
        status_enum = ApprovalStatus(status) if status else None
        requests = app.approval_queue.list(status=status_enum)
        settings = load_settings()
        return {
            "approvals": [
                approval_to_client_dict(
                    r,
                    touch_id_policy_enabled=settings.require_touch_id_for_high_risk,
                )
                for r in requests
            ],
        }

    async def approval_show(params: dict[str, Any]) -> dict[str, Any]:
        request = app.approval_queue.get(int(params["id"]))
        settings = load_settings()
        return approval_to_client_dict(
            request,
            touch_id_policy_enabled=settings.require_touch_id_for_high_risk,
        )

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
        existing = app.approval_queue.get(int(params["id"]))
        if existing.status != ApprovalStatus.PENDING:
            result = existing.to_dict()
            result["already_decided"] = True
            return result
        request = await app.approval_queue.deny(
            int(params["id"]),
            decided_by=str(params.get("decided_by", "user")),
            reason=str(params.get("reason", "")),
        )
        return request.to_dict()

    async def approval_defer(params: dict[str, Any]) -> dict[str, Any]:
        existing = app.approval_queue.get(int(params["id"]))
        if existing.status != ApprovalStatus.PENDING:
            result = existing.to_dict()
            result["already_decided"] = True
            return result
        request = await app.approval_queue.defer(int(params["id"]))
        return request.to_dict()

    async def approval_respond_elicitation(params: dict[str, Any]) -> dict[str, Any]:
        response_value = params.get("response_value")
        if not isinstance(response_value, dict):
            raise ValueError("response_value must be an object")
        request = app.approval_queue.get(int(params["id"]))
        if request.action is not ApprovalAction.ELICITATION:
            raise ValueError(f"approval {request.id} is not an elicitation")
        completed = await app.approval_queue.complete_elicitation(
            request.id,
            response_value=response_value,
            decided_by=str(params.get("decided_by", "user")),
        )
        return completed.to_dict()

    async def approval_approve(params: dict[str, Any]) -> dict[str, Any]:
        identity = int(params["id"])
        if app.approval_queue.get(identity).status != ApprovalStatus.PENDING:
            return await approve_locked(params)
        async with decision_locks.setdefault(identity, asyncio.Lock()):
            return await approve_locked(params)

    async def approve_locked(params: dict[str, Any]) -> dict[str, Any]:
        request = app.approval_queue.get(int(params["id"]))
        if request.status == ApprovalStatus.SUPERSEDED:
            raise ValueError("This review changed; request a fresh review")
        if request.status != ApprovalStatus.PENDING:
            return {"approval": request.to_dict(), "already_decided": True}
        if request.action == ApprovalAction.SEND_EMAIL:
            _email_args(request.payload, request.target)
            _validate_reviewed_draft(app, request)
        decided_by = str(params.get("decided_by", "user"))
        settings = load_settings()
        authentication = None
        if settings.require_touch_id_for_high_risk and _approval_requires_strong_auth(request):
            authentication = await authenticate_approval(request)
            current = app.approval_queue.get(request.id)
            if current != request or current.status != ApprovalStatus.PENDING:
                raise ValueError(
                    "The review changed or expired during authentication; prepare a fresh review"
                )
            if request.action == ApprovalAction.SEND_EMAIL:
                _validate_reviewed_draft(app, request)

        decision_scope = params.get("decision_scope")
        if decision_scope is not None and not isinstance(decision_scope, dict):
            raise ValueError("decision_scope must be an object")
        decision_scope = dict(decision_scope or {})
        for reserved in ("authentication", "execution_status", "execution_detail"):
            decision_scope.pop(reserved, None)
        if authentication is not None:
            decision_scope["authentication"] = authentication
        approved = await app.approval_queue.approve(
            request.id,
            decided_by=decided_by,
            decision_scope=decision_scope,
        )

        if approved.action == ApprovalAction.SEND_EMAIL:
            try:
                new_session, dispatch_outcome = await _execute_declassified_email(
                    app,
                    approved.payload,
                    approved.target,
                    origin_session=approved.from_session,
                    approval_id=approved.id,
                    source_labels=approved.labels_in,
                )
            finally:
                ref = _reviewed_draft_ref(approved.payload)
                if (
                    ref
                    and app.email_operations is not None
                    and json.loads(approved.payload).get("format") == "capdep.email.v2"
                    and app.email_operations.for_draft(UUID(ref[0]), ref[1]) is None
                ):
                    app.approval_queue.mark_not_submitted(
                        approved.id, "Stopped before submission. Prepare a fresh review."
                    )
            approved = app.approval_queue.get(approved.id)
            draft_ref = _reviewed_draft_ref(approved.payload)
            if draft_ref and dispatch_outcome.output and dispatch_outcome.output.get("delivered"):
                app.email_drafts.discard(UUID(draft_ref[0]))
            updated = replace(approved, to_session=new_session)
            app.approval_queue.record(updated)
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
            app.approval_queue.record(updated)
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
            app.approval_queue.record(updated)
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
        strong_auth = str(params.get("strong_auth", ""))
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
                    {"id": m.id, "decided_by": decided_by, "strong_auth": strong_auth},
                )
                if result.get("already_decided"):
                    # Another client can decide a later sibling while an
                    # earlier one awaits authentication or dispatch. Count the
                    # authoritative result, not the stale pending snapshot.
                    result["skipped"] = True
                    result["reason"] = f"status={result['approval']['status']}"
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

    async def email_draft_list(params: dict[str, Any]) -> dict[str, Any]:
        outcome = await app.tool_client.call_tool(
            UUID(str(params["session_id"])),
            "email.draft_list",
            {},
        )
        if outcome.output is None or outcome.error:
            raise ValueError("Draft read is not authorized")
        return outcome.output

    async def email_source_list(params: dict[str, Any]) -> dict[str, Any]:
        outcome = await app.tool_client.call_tool(
            UUID(str(params["session_id"])), "email.source_list", {}
        )
        if outcome.output is None or outcome.error:
            raise ValueError("Source read is not authorized")
        return outcome.output

    async def email_reply_create(params: dict[str, Any]) -> dict[str, Any]:
        await email_source_list(params)
        outcome = await app.tool_client.call_tool(
            UUID(str(params["session_id"])),
            "email.reply_create",
            {key: params[key] for key in ("source_id", "body") if key in params},
        )
        if outcome.output is None or outcome.error:
            raise ValueError(outcome.error or "Reply creation is not authorized")
        return outcome.output

    async def email_draft_update(params: dict[str, Any]) -> dict[str, Any]:
        await email_draft_list(params)
        _withdraw_draft_reviews(
            app, str(params["session_id"]), str(params["id"]), int(params["version"])
        )
        outcome = await app.tool_client.call_tool(
            UUID(str(params["session_id"])),
            "email.draft_update",
            {key: params[key] for key in ("id", "version", "to", "subject", "body")},
        )
        if outcome.output is None or outcome.error:
            raise ValueError(outcome.error or "Draft editing is not authorized")
        return outcome.output

    async def email_draft_begin_edit(params: dict[str, Any]) -> dict[str, Any]:
        await email_draft_list(params)
        draft = app.email_drafts.get(UUID(str(params["id"])))
        if draft is None or str(draft.session_id) != str(params["session_id"]):
            raise ValueError("Draft not found in this conversation")
        if draft.version != int(params["version"]):
            raise ValueError("The draft changed; reload before editing")
        _withdraw_draft_reviews(app, str(draft.session_id), str(draft.id), draft.version)
        return {
            "draft": {
                "id": str(draft.id),
                "version": draft.version,
                "to": draft.to,
                "subject": draft.subject,
                "body": draft.body,
                "source_id": draft.source.get("source_id", ""),
                "account_id": draft.source.get("account_id", ""),
                "from_addr": draft.source.get("from_addr", ""),
            }
        }

    async def approval_task_list(params: dict[str, Any]) -> dict[str, Any]:
        session_id = UUID(str(params["session_id"]))
        # Use the same deterministic, labeled read path as the email workspace.
        await email_draft_list(params)
        settings = load_settings()
        reviews = []
        for request in app.approval_queue.list():
            if request.from_session != session_id:
                continue
            row = approval_to_client_dict(
                request, touch_id_policy_enabled=settings.require_touch_id_for_high_risk
            )
            row["review_state"] = request.status.value
            row["review_error"] = ""
            if request.decision_scope.get("execution_status") == "failed_before_submission":
                row["review_state"] = "failed_before_submission"
                row["review_error"] = request.decision_scope.get("execution_detail", "")
            row["can_approve"] = request.status == ApprovalStatus.PENDING
            ref = (
                _reviewed_draft_ref(request.payload)
                if request.action == ApprovalAction.SEND_EMAIL
                else None
            )
            if (
                request.status == ApprovalStatus.PENDING
                and request.action == ApprovalAction.SEND_EMAIL
            ):
                try:
                    _validate_reviewed_draft(app, request)
                except ValueError as error:
                    row.update(review_state="changed", review_error=str(error), can_approve=False)
            if ref and app.email_operations is not None:
                operation = app.email_operations.for_draft(UUID(ref[0]), ref[1])
                if operation is not None:
                    row["review_state"] = operation.reconciliation_outcome or operation.status
                    row["can_approve"] = False
                    row["operation"] = {
                        "id": str(operation.id),
                        "status": operation.status,
                        "detail": operation.detail,
                        "reconciliation_outcome": operation.reconciliation_outcome,
                        "reconciliation_reason": operation.reconciliation_reason,
                    }
            reviews.append(row)
        return {"reviews": reviews}

    async def email_operation_list(params: dict[str, Any]) -> dict[str, Any]:
        journal = app.email_operations
        if journal is None:
            raise ValueError("Email operation storage is not ready")
        # Same source-session read authorization, including its conservative labels.
        await email_draft_list(params)
        return {
            "operations": [
                {
                    "id": str(o.id),
                    "draft_id": str(o.draft_id),
                    "draft_version": o.draft_version,
                    "message_id": o.message_id,
                    # Frozen reviewed identity lets the operator distinguish receipts
                    # without exposing message bodies in the history projection.
                    "subject": o.envelope.get("subject", ""),
                    "to": o.envelope.get("to", ""),
                    "recipient": o.envelope.get("to", ""),
                    "from_addr": o.envelope.get("from_addr", ""),
                    "account_id": o.envelope.get("account_id", ""),
                    "in_reply_to": o.envelope.get("in_reply_to", ""),
                    "references": o.envelope.get("references", ""),
                    "status": o.status,
                    "detail": o.detail,
                    "created_at": o.created_at.isoformat(),
                    "updated_at": o.updated_at.isoformat(),
                    "reconciliation_outcome": o.reconciliation_outcome,
                    "reconciliation_reason": o.reconciliation_reason,
                    "reconciled_by": o.reconciled_by,
                    "reconciled_at": o.reconciled_at.isoformat() if o.reconciled_at else None,
                }
                for o in journal.all(UUID(str(params["session_id"])))
            ]
        }

    async def email_operation_status(params: dict[str, Any]) -> dict[str, Any]:
        rows = (await email_operation_list(params))["operations"]
        operation = next((row for row in rows if row["id"] == str(params["id"])), None)
        if operation is None:
            raise ValueError("Operation not found in this session")
        return {"operation": operation}

    async def email_operation_reconcile(params: dict[str, Any]) -> dict[str, Any]:
        await email_operation_status(params)
        if params.get("confirmed") is not True:
            raise ValueError("Explicit provider-verification confirmation is required")
        if params.get("outcome") not in {"confirmed_not_sent", "smtp_accepted"}:
            raise ValueError("Choose a provider-verified outcome")
        reason = params.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Describe the provider evidence for this reconciliation")
        journal = app.email_operations
        if journal is None:
            raise ValueError("Email operation storage is not ready")
        journal.reconcile(
            UUID(str(params["id"])), params["outcome"], reason=reason, decided_by="operator"
        )
        return await email_operation_status(params)

    async def email_draft_review(params: dict[str, Any]) -> dict[str, Any]:
        session_id = UUID(str(params["session_id"]))
        # Discovery/read still goes through decide(), not a UI-side permission.
        outcome = await app.tool_client.call_tool(session_id, "email.draft_list", {})
        if outcome.output is None or outcome.error:
            raise ValueError("Draft read is not authorized")
        draft = app.email_drafts.get(UUID(str(params["id"])))
        if draft is None or draft.session_id != session_id:
            raise ValueError("Draft not found in this session")
        if draft.version != int(params["version"]):
            raise ValueError("Draft changed; reload before requesting review")
        session = app.graph.get(session_id)
        labels = label_join(session.label_state, draft.label_state)
        # This release path is for operator-owned personal mail, not arbitrary
        # structural denials or confidential categories governed by other policies.
        if any(
            tag.category != "personal" or is_above(tag.tier, Tier.REGULATED) for tag in labels.a
        ):
            raise ValueError("This draft requires a category-specific release policy")
        if draft.source:
            if app.email_sources.get(draft.source["source_id"], draft.session_id) is None:
                raise ValueError("Source mailbox changed; reread the message before review")
            config = app._email_smtp_config
            if (
                config is None
                or smtp_config_fingerprint(config) != draft.source["account_fingerprint"]
            ):
                raise ValueError("Sending account changed or unavailable; create a fresh reply")
            journal = app.email_operations
            if journal is None:
                raise ValueError("Email operation storage is not ready")
            if journal.blocks_draft(draft.id):
                raise ValueError(
                    "This draft has an unresolved or accepted submission; inspect its status"
                )
            existing = journal.for_draft(draft.id, draft.version)
            if existing is not None:
                raise ValueError(
                    "This draft version already has a send operation; inspect its status"
                )
            message = {
                "account_id": draft.source["account_id"],
                "from_addr": draft.source["from_addr"],
                "to": draft.to,
                "subject": draft.subject,
                "body": draft.body,
                "in_reply_to": draft.source["in_reply_to"],
                "references": draft.source["references"],
                "message_id": f"<{draft.id}.v{draft.version}@capabledeputy.local>",
            }
            envelope = {
                "format": "capdep.email.v2",
                "draft_id": str(draft.id),
                "draft_version": draft.version,
                "source_id": draft.source["source_id"],
                "account_fingerprint": draft.source["account_fingerprint"],
                "envelope_digest": stable_digest(message),
                "message": message,
            }
        else:
            if app._email_smtp_config is not None:
                raise ValueError("Select an actual source message to create a reviewed reply")
            envelope = {
                "format": "capdep.email.v1",
                "draft_id": str(draft.id),
                "draft_version": draft.version,
                "message": {"to": draft.to, "subject": draft.subject, "body": draft.body},
            }
        payload = json.dumps(envelope, sort_keys=True)
        _email_args(payload, draft.to)
        # One actionable review per saved version. Reopening never resets expiry.
        for previous in app.approval_queue.list():
            if (
                previous.from_session == session_id
                and previous.payload == payload
                and previous.status == ApprovalStatus.PENDING
            ):
                return previous.to_dict()
        request = await app.approval_queue.submit(
            from_session=session_id,
            action=ApprovalAction.SEND_EMAIL,
            payload=payload,
            target=draft.to,
            labels_in=labels,
            justification="Review exact saved draft; release only this message to this recipient",
            rule="supervised-personal-mail-release",
            allow_patterns=False,
        )
        return request.to_dict()

    return {
        "email.source_list": email_source_list,
        "email.reply_create": email_reply_create,
        "email.draft_update": email_draft_update,
        "email.draft_begin_edit": email_draft_begin_edit,
        "approval.task_list": approval_task_list,
        "email.operation_list": email_operation_list,
        "email.operation_status": email_operation_status,
        "email.operation_reconcile": email_operation_reconcile,
        "email.draft_list": email_draft_list,
        "email.draft_review": email_draft_review,
        "approval.list": approval_list,
        "approval.show": approval_show,
        "approval.submit": approval_submit,
        "approval.approve": approval_approve,
        "approval.approve_group": approval_approve_group,
        "approval.deny": approval_deny,
        "approval.defer": approval_defer,
        "approval.respond_elicitation": approval_respond_elicitation,
    }


def _approval_requires_strong_auth(request: Any) -> bool:
    return approval_requires_strong_auth(request)


def _withdraw_draft_reviews(app: App, session_id: str, draft_id: str, version: int) -> None:
    """No await between inspecting and withdrawing every old review."""
    journal = app.email_operations
    if journal is not None and journal.blocks_draft(UUID(draft_id)):
        raise ValueError(
            "This reply has a submitted or uncertain send; "
            "inspect its unresolved operation before editing"
        )
    related = [
        r
        for r in app.approval_queue.list()
        if str(r.from_session) == session_id
        and r.action == ApprovalAction.SEND_EMAIL
        and _reviewed_draft_ref(r.payload) == (draft_id, version)
    ]
    for request in related:
        if request.status == ApprovalStatus.APPROVED:
            if request.decision_scope.get("execution_status") == "failed_before_submission":
                continue
            operation = journal.for_draft(UUID(draft_id), version) if journal is not None else None
            if operation is None or (
                operation.status != "failed_before_submission"
                and operation.reconciliation_outcome != "confirmed_not_sent"
            ):
                raise ValueError(
                    "This reply was already approved; check its send status before editing"
                )
    for request in related:
        if request.status == ApprovalStatus.PENDING:
            app.approval_queue.supersede(request.id)


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

    outcome = await app.tool_client.call_tool(
        granted.id,
        tool_name,
        tool_args,
        already_approved=True,
    )
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
    outcome = await app.tool_client.call_tool(
        granted.id,
        "purchase.queue",
        call_args,
        already_approved=True,
    )
    await app.graph.abort(granted.id)
    return granted.id, outcome


async def _execute_declassified_email(
    app: App,
    payload: str,
    target: str,
    origin_session: UUID,
    approval_id: int | None = None,
    source_labels: LabelState | None = None,
) -> tuple[UUID, Any]:
    args = _email_args(payload, target)
    ref = _reviewed_draft_ref(payload)
    if ref and "account_id" in args:
        args.update(
            _draft_id=ref[0],
            _draft_version=ref[1],
            _origin_session=str(origin_session),
            _reviewed_labels=source_labels.to_dict() if source_labels else {},
        )
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
    if approval_id is not None:
        recorder = ProvenanceRecorder(app.audit)
        node_id = f"reviewed_email_release:{purpose_session.id}"
        await recorder.node(
            session_id=origin_session,
            node_id=node_id,
            kind="reviewed_email_release",
            label_state=source_labels,
            metadata={
                "approval_id": approval_id,
                "envelope_digest": stable_digest(args),
                "target": target,
                "execution_session": str(purpose_session.id),
            },
        )
        await recorder.edge(
            session_id=origin_session,
            from_node_id=approval_request_node_id(approval_id),
            to_node_id=node_id,
            kind="authorized_exact_release",
        )

    outcome = await app.tool_client.call_tool(
        granted.id,
        "email.send",
        args,
        already_approved=True,
    )

    await app.graph.abort(granted.id)
    return granted.id, outcome


def _email_args(payload: str, target: str) -> dict[str, Any]:
    try:
        envelope = json.loads(payload)
    except json.JSONDecodeError:
        envelope = None
    if isinstance(envelope, dict) and envelope.get("format") == "capdep.email.v2":
        args = envelope.get("message")
        expected = {
            "account_id",
            "from_addr",
            "to",
            "subject",
            "body",
            "in_reply_to",
            "references",
            "message_id",
        }
        if (
            not isinstance(args, dict)
            or set(args) != expected
            or not all(isinstance(v, str) for v in args.values())
        ):
            raise ValueError("Reviewed reply must contain the complete exact envelope")
        if args["to"] != target or single_recipient(args["to"]) != target:
            raise ValueError("Reviewed reply recipient does not match target")
        if stable_digest(args) != envelope.get("envelope_digest"):
            raise ValueError("Reviewed reply digest does not match")
        if any(c in value for key, value in args.items() if key != "body" for c in "\r\n"):
            raise ValueError("Invalid reply headers")
        args = {**args, "account_fingerprint": str(envelope["account_fingerprint"])}
    elif isinstance(envelope, dict) and envelope.get("format") in {
        "capdep.email.v1",
        "capdep.email.v2",
    }:
        args = envelope.get("message")
        if not isinstance(args, dict) or set(args) != {"to", "subject", "body"}:
            raise ValueError("Reviewed email must contain exactly to, subject and body")
        if not all(isinstance(v, str) for v in args.values()) or args["to"] != target:
            raise ValueError("Reviewed email recipient does not match approval target")
    else:
        # Existing explicit body-only approvals retain their historical meaning.
        args = {"to": target, "subject": "Approved declassified message", "body": payload}
    if not target or any(c in args["to"] + args["subject"] for c in "\r\n"):
        raise ValueError("Invalid email recipient or subject")
    return dict(args)


def _reviewed_draft_ref(payload: str) -> tuple[str, int] | None:
    try:
        envelope = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if (
        isinstance(envelope, dict)
        and envelope.get("format") in {"capdep.email.v1", "capdep.email.v2"}
        and "draft_id" in envelope
    ):
        return str(envelope["draft_id"]), int(envelope["draft_version"])
    return None


def _validate_reviewed_draft(app: App, request: Any) -> None:
    ref = _reviewed_draft_ref(request.payload)
    if ref is None:
        return
    draft = app.email_drafts.get(UUID(ref[0]))
    if draft is None or draft.session_id != request.from_session or draft.version != ref[1]:
        raise ValueError("Reviewed draft changed or disappeared; request a fresh review")
    args = _email_args(request.payload, request.target)
    if draft.source:
        envelope = json.loads(request.payload)
        if envelope.get("source_id") != draft.source["source_id"]:
            raise ValueError("Reviewed source does not match saved draft")
        if app.email_sources.get(draft.source["source_id"], draft.session_id) is None:
            raise ValueError("Reviewed source is unavailable")
        config = app._email_smtp_config
        if config is None or smtp_config_fingerprint(config) != draft.source["account_fingerprint"]:
            raise ValueError("Sending account changed; request a fresh review")
        source_session = app.graph.get(draft.session_id)
        if source_session.status.value not in {"active", "waiting_approval"}:
            raise ValueError("Source session is no longer active")
        labels = label_join(source_session.label_state, draft.label_state)
        if (
            any(
                tag.category != "personal" or is_above(tag.tier, Tier.REGULATED) for tag in labels.a
            )
            or labels != request.labels_in
        ):
            raise ValueError("Source labels changed; request a fresh review")
        expected = {
            "to": draft.to,
            "subject": draft.subject,
            "body": draft.body,
            "account_id": draft.source["account_id"],
            "from_addr": draft.source["from_addr"],
            "in_reply_to": draft.source["in_reply_to"],
            "references": draft.source["references"],
            "message_id": f"<{draft.id}.v{draft.version}@capabledeputy.local>",
            "account_fingerprint": draft.source["account_fingerprint"],
        }
    else:
        expected = {"to": draft.to, "subject": draft.subject, "body": draft.body}
    if args != expected:
        raise ValueError("Reviewed draft content does not match saved draft")
