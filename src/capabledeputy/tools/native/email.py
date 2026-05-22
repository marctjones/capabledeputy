"""Email tools — send + draft workflow (DESIGN.md §7.4).

Send remains a stub that records to an EmailOutbox; drafts are an
in-memory DraftBox separate from the outbox. The split matters
structurally: drafts are LOCAL (non-egressing) so they don't trip the
social-commitment gate. The send action is what crosses the boundary.

Phase 7+ ships an upstream Gmail MCP server adapter that subsumes
both.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from capabledeputy.approval.model import ApprovalAction
from capabledeputy.approval.route import ApprovalPayloadKind, ApprovalRoute
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolResult


@dataclass(frozen=True)
class SentEmail:
    id: UUID
    session_id: UUID
    to: str
    subject: str
    body: str
    sent_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Draft:
    id: UUID
    session_id: UUID
    to: str
    subject: str
    body: str
    created_at: datetime
    updated_at: datetime


class EmailOutbox:
    def __init__(self) -> None:
        self._sent: list[SentEmail] = []

    def all(self) -> list[SentEmail]:
        return list(self._sent)

    def append(self, email: SentEmail) -> None:
        self._sent.append(email)


class DraftBox:
    """In-memory draft store. Holds Drafts keyed by id. Drafts are
    local-only — they NEVER egress on their own. Promoting a draft to
    a real send requires going through email.send (or the dedicated
    email.draft_send tool), which routes through the same policy
    chokepoint as a fresh send."""

    def __init__(self) -> None:
        self._drafts: dict[UUID, Draft] = {}

    def save(
        self,
        *,
        session_id: UUID,
        to: str,
        subject: str,
        body: str,
    ) -> Draft:
        now = datetime.now(UTC)
        draft = Draft(
            id=uuid4(),
            session_id=session_id,
            to=to,
            subject=subject,
            body=body,
            created_at=now,
            updated_at=now,
        )
        self._drafts[draft.id] = draft
        return draft

    def update(self, draft_id: UUID, **fields: Any) -> Draft | None:
        existing = self._drafts.get(draft_id)
        if existing is None:
            return None
        updated = replace(existing, updated_at=datetime.now(UTC), **fields)
        self._drafts[draft_id] = updated
        return updated

    def get(self, draft_id: UUID) -> Draft | None:
        return self._drafts.get(draft_id)

    def discard(self, draft_id: UUID) -> bool:
        return self._drafts.pop(draft_id, None) is not None

    def all(self) -> list[Draft]:
        return sorted(self._drafts.values(), key=lambda d: d.updated_at, reverse=True)


def make_email_tools(outbox: EmailOutbox, drafts: DraftBox) -> list[ToolDefinition]:
    async def email_send(args: dict[str, Any], context: ToolContext) -> ToolResult:
        sent = SentEmail(
            id=uuid4(),
            session_id=context.session_id,
            to=str(args.get("to", "")),
            subject=str(args.get("subject", "")),
            body=str(args.get("body", "")),
            sent_at=datetime.now(UTC),
        )
        outbox.append(sent)
        return ToolResult(
            output={
                "sent": True,
                "id": str(sent.id),
                "to": sent.to,
                "subject": sent.subject,
            },
        )

    async def email_draft_save(args: dict[str, Any], context: ToolContext) -> ToolResult:
        draft = drafts.save(
            session_id=context.session_id,
            to=str(args.get("to", "")),
            subject=str(args.get("subject", "")),
            body=str(args.get("body", "")),
        )
        return ToolResult(
            output={
                "saved": True,
                "id": str(draft.id),
                "to": draft.to,
                "subject": draft.subject,
            },
        )

    async def email_draft_list(_args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        return ToolResult(
            output={
                "drafts": [
                    {
                        "id": str(d.id),
                        "to": d.to,
                        "subject": d.subject,
                        "updated_at": d.updated_at.isoformat(),
                    }
                    for d in drafts.all()
                ],
            },
        )

    async def email_draft_send(args: dict[str, Any], context: ToolContext) -> ToolResult:
        try:
            draft_id = UUID(str(args["id"]))
        except (KeyError, ValueError) as e:
            return ToolResult(output={"sent": False, "error": f"bad id: {e}"})
        draft = drafts.get(draft_id)
        if draft is None:
            return ToolResult(output={"sent": False, "error": "unknown draft id"})
        sent = SentEmail(
            id=uuid4(),
            session_id=context.session_id,
            to=draft.to,
            subject=draft.subject,
            body=draft.body,
            sent_at=datetime.now(UTC),
        )
        outbox.append(sent)
        drafts.discard(draft_id)
        return ToolResult(
            output={
                "sent": True,
                "id": str(sent.id),
                "to": sent.to,
                "subject": sent.subject,
                "promoted_from_draft": str(draft_id),
            },
        )

    return [
        ToolDefinition(
            name="email.send",
            description=(
                "Send an email. Stub: records the send for audit but "
                "does not actually deliver. Required args: to, subject, body."
            ),
            capability_kind=CapabilityKind.SEND_EMAIL,
            handler=email_send,
            target_arg="to",
            effect_class="social.send_email",
            default_reversibility={"degree": "irreversible", "agent": "external"},
            social_commitment=True,
            tool_provenance="operator-curated",
            surfaces_destination_id=True,
            approval_route=ApprovalRoute(
                action=ApprovalAction.SEND_EMAIL,
                target_arg="to",
                payload_kind=ApprovalPayloadKind.BODY_ARG,
                payload_arg="body",
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["to", "subject", "body"],
            },
        ),
        ToolDefinition(
            name="email.draft_save",
            description=(
                "Save a local email draft. NON-EGRESSING — the draft lives "
                "in the local DraftBox; the social-commitment gate does NOT "
                "fire. Required args: to, subject, body."
            ),
            capability_kind=CapabilityKind.CREATE_FS,
            handler=email_draft_save,
            target_arg="to",
            effect_class="data.create_local",
            default_reversibility={"degree": "reversible", "agent": "system"},
            tool_provenance="operator-curated",
            parameters_schema={
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["to", "subject", "body"],
            },
        ),
        ToolDefinition(
            name="email.draft_list",
            description="List saved drafts. Read-only.",
            capability_kind=CapabilityKind.IMAP_READ,
            handler=email_draft_list,
            effect_class="data.read_local",
            default_reversibility={"degree": "reversible", "agent": "system"},
            tool_provenance="operator-curated",
            parameters_schema={"type": "object", "properties": {}},
        ),
        ToolDefinition(
            name="email.draft_send",
            description=(
                "Promote a saved draft to a real send. Same policy gates "
                "as email.send (irreversible/external social commitment). "
                "Removes the draft on success. Required args: id (string). "
                "Caps for this tool are coarse (target is the wildcard) "
                "because the draft's recipient is not knowable until "
                "lookup time; for fine-grained recipient gating use "
                "email.send directly."
            ),
            capability_kind=CapabilityKind.SEND_EMAIL,
            handler=email_draft_send,
            # No target_arg: the action target is "", caps must be
            # wildcard-pattern'd, overrides target "".
            effect_class="social.send_email",
            default_reversibility={"degree": "irreversible", "agent": "external"},
            social_commitment=True,
            tool_provenance="operator-curated",
            surfaces_destination_id=True,
            parameters_schema={
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        ),
    ]
