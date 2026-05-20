"""Email send stub tool (DESIGN.md §7.4).

In v0.1 this records sends rather than actually emailing. Phase 7+
ships an upstream Gmail MCP server adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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


class EmailOutbox:
    def __init__(self) -> None:
        self._sent: list[SentEmail] = []

    def all(self) -> list[SentEmail]:
        return list(self._sent)

    def append(self, email: SentEmail) -> None:
        self._sent.append(email)


def make_email_tools(outbox: EmailOutbox) -> list[ToolDefinition]:
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

    return [
        ToolDefinition(
            name="email.send",
            description=(
                "Send an email. In v0.1 this is a stub that records the "
                "send for audit but does not actually deliver. Required "
                "args: to (string), subject (string), body (string)."
            ),
            capability_kind=CapabilityKind.SEND_EMAIL,
            handler=email_send,
            target_arg="to",
            # 003 T012-full — declare v2 four-axis decision fields.
            # social.send_email is in the FR-019 social-commitment
            # set; the reversibility gate will force-irreversible
            # regardless of what we declare here, but declaring it
            # makes the audit/explain story honest.
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
                    "to": {"type": "string", "description": "Recipient address."},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["to", "subject", "body"],
            },
        ),
    ]
