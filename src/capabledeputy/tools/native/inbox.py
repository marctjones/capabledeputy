"""Inbound-email tool stub (DESIGN.md §7.4 — `untrusted.external` source).

Demo-grade stub for inbox reads. Real deployments should wrap a Gmail
or IMAP MCP server via `upstream/` so the same labels apply. The point
of having a native stub is that demos can run deterministically and
the labels we care about (`untrusted.external`) are on data we control.

Two tools:

  - `inbox.list` — listing of unread message metadata, labeled
    `untrusted.external` because senders are not the user.
  - `inbox.read` — read one message by id, labeled `untrusted.external`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.labels import Label
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolResult


@dataclass(frozen=True)
class InboundMessage:
    id: str
    sender: str
    subject: str
    body: str
    received_at: datetime
    unread: bool = True


class Inbox:
    def __init__(self) -> None:
        self._messages: dict[str, InboundMessage] = {}

    def add(self, message: InboundMessage) -> None:
        self._messages[message.id] = message

    def all(self) -> list[InboundMessage]:
        return sorted(self._messages.values(), key=lambda m: m.received_at)

    def get(self, message_id: str) -> InboundMessage | None:
        return self._messages.get(message_id)

    def mark_read(self, message_id: str) -> None:
        existing = self._messages.get(message_id)
        if existing is None:
            return
        from dataclasses import replace as _replace

        self._messages[message_id] = _replace(existing, unread=False)


def make_inbox_tools(inbox: Inbox) -> list[ToolDefinition]:
    async def inbox_list(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        unread_only = bool(args.get("unread_only", True))
        messages = [m for m in inbox.all() if (not unread_only) or m.unread]
        return ToolResult(
            output={
                "messages": [
                    {
                        "id": m.id,
                        "sender": m.sender,
                        "subject": m.subject,
                        "received_at": m.received_at.isoformat(),
                        "unread": m.unread,
                    }
                    for m in messages
                ],
            },
            additional_labels=frozenset({Label.UNTRUSTED_EXTERNAL}),
        )

    async def inbox_read(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        msg_id = str(args["id"])
        message = inbox.get(msg_id)
        if message is None:
            return ToolResult(output={"found": False})
        inbox.mark_read(msg_id)
        return ToolResult(
            output={
                "found": True,
                "id": message.id,
                "sender": message.sender,
                "subject": message.subject,
                "body": message.body,
                "received_at": message.received_at.isoformat(),
            },
            additional_labels=frozenset({Label.UNTRUSTED_EXTERNAL}),
        )

    return [
        ToolDefinition(
            name="inbox.list",
            effect_class="data.read_inbox",
            default_reversibility={"degree": "reversible", "agent": "system"},
            tool_provenance="operator-curated",
            description=(
                "List inbound messages. Returns untrusted.external-labeled "
                "metadata. Required args: unread_only (bool, default true)."
            ),
            capability_kind=CapabilityKind.READ_FS,
            handler=inbox_list,
            target_arg="folder",
            inherent_labels=frozenset({Label.UNTRUSTED_EXTERNAL}),
            parameters_schema={
                "type": "object",
                "properties": {
                    "unread_only": {"type": "boolean"},
                },
            },
        ),
        ToolDefinition(
            name="inbox.read",
            effect_class="data.read_inbox",
            default_reversibility={"degree": "reversible", "agent": "system"},
            tool_provenance="operator-curated",
            description=(
                "Read one inbound message by id. The body is labeled "
                "untrusted.external; reading propagates that label into "
                "the calling session. Required args: id (string)."
            ),
            capability_kind=CapabilityKind.READ_FS,
            handler=inbox_read,
            target_arg="id",
            inherent_labels=frozenset({Label.UNTRUSTED_EXTERNAL}),
            parameters_schema={
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        ),
    ]


def _utcnow() -> datetime:
    return datetime.now(UTC)
