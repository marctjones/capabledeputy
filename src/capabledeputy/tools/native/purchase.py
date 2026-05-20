"""Purchase-queue stub tool (DESIGN.md §7.4 / Clark-Wilson approval gate).

Always returns 'queued for approval'. The real purchase-queue + approval
workflow lands in Phase 5; this stub is the placeholder that lets the
canonical scenario (untrusted-email-tries-to-purchase, §13) demonstrate
that the policy engine refuses unilateral purchases regardless of what
the LLM tries to do.
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
class QueuedPurchase:
    id: UUID
    session_id: UUID
    vendor: str
    item: str
    amount: int | None
    queued_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


class PurchaseQueue:
    def __init__(self) -> None:
        self._queue: list[QueuedPurchase] = []

    def all(self) -> list[QueuedPurchase]:
        return list(self._queue)

    def append(self, purchase: QueuedPurchase) -> None:
        self._queue.append(purchase)


def make_purchase_tools(queue: PurchaseQueue) -> list[ToolDefinition]:
    async def purchase_queue_handler(
        args: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        purchase = QueuedPurchase(
            id=uuid4(),
            session_id=context.session_id,
            vendor=str(args.get("vendor", "")),
            item=str(args.get("item", "")),
            amount=int(args["amount"]) if args.get("amount") is not None else None,
            queued_at=datetime.now(UTC),
        )
        queue.append(purchase)
        return ToolResult(
            output={
                "queued": True,
                "id": str(purchase.id),
                "vendor": purchase.vendor,
                "item": purchase.item,
                "amount": purchase.amount,
                "message": "queued for human approval",
            },
        )

    return [
        ToolDefinition(
            name="purchase.queue",
            effect_class="social.queue_purchase",
            default_reversibility={"degree": "irreversible", "agent": "external"},
            social_commitment=True,
            tool_provenance="operator-curated",
            surfaces_destination_id=True,
            description=(
                "Queue a purchase for human approval. Does not actually buy "
                "anything; the request is recorded for the user to review. "
                "Required args: vendor (string), item (string), amount "
                "(integer, dollars)."
            ),
            capability_kind=CapabilityKind.QUEUE_PURCHASE,
            handler=purchase_queue_handler,
            target_arg="vendor",
            amount_arg="amount",
            approval_route=ApprovalRoute(
                action=ApprovalAction.QUEUE_PURCHASE,
                target_arg="vendor",
                payload_kind=ApprovalPayloadKind.JSON_ARGS,
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "vendor": {"type": "string", "description": "Vendor name."},
                    "item": {"type": "string", "description": "What to buy."},
                    "amount": {
                        "type": "integer",
                        "description": "Amount in whole dollars.",
                    },
                },
                "required": ["vendor", "item", "amount"],
            },
        ),
    ]
