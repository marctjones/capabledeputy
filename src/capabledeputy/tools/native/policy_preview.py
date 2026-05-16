"""Read-only policy dry-run tool for the agent.

`policy.preview` lets the agent predict whether a future tool call
would be allowed, denied, or gated by approval — without actually
dispatching it. The agent uses this to plan multi-step tasks: rather
than promise the user "I'll send a summary" and then discover that
egress is blocked, the agent checks first.

The tool itself is gated by `READ_FS` because every scenario grants
that already; it has no inherent labels (introspection doesn't taint
the session) and no side effects (it just calls `decide()`).
"""

from __future__ import annotations

from typing import Any

from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.engine import decide
from capabledeputy.session.graph import SessionGraph
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolResult


def make_policy_preview_tools(graph: SessionGraph) -> list[ToolDefinition]:
    async def preview(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            kind = CapabilityKind(str(args["kind"]).upper())
        except (KeyError, ValueError) as e:
            return ToolResult(output={"error": f"unknown kind: {e}"})
        target = str(args.get("target", ""))
        amount_raw = args.get("amount")
        amount = int(amount_raw) if amount_raw is not None else None

        session = graph.get(ctx.session_id)
        action = Action(kind=kind, target=target, amount=amount)
        decision = decide(
            session.label_set,
            session.capability_set,
            action,
            used_kinds=session.used_kinds,
        )
        return ToolResult(
            output={
                "decision": decision.decision.value,
                "rule": decision.rule,
                "reason": decision.reason,
                "would_match_capability": decision.matched_capability is not None,
                "effective_labels": sorted(
                    label.value for label in decision.effective_labels
                ),
            },
        )

    return [
        ToolDefinition(
            name="policy.preview",
            description=(
                "Predict whether a tool call would be allowed, denied, or "
                "require approval — without dispatching it. Pass `kind` "
                "(CapabilityKind value, e.g. SEND_EMAIL), `target` (e.g. "
                "the recipient or path), and optionally `amount` for "
                "purchases. Use this to plan: if the preview returns "
                "decision='deny', the actual call will also be denied — "
                "don't waste a turn trying."
            ),
            capability_kind=CapabilityKind.READ_FS,
            handler=preview,
            target_arg="kind",
            parameters_schema={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "description": "CapabilityKind value to test",
                    },
                    "target": {
                        "type": "string",
                        "description": "Action target (recipient, path, etc)",
                    },
                    "amount": {
                        "type": "integer",
                        "description": "Optional amount (for purchases)",
                    },
                },
                "required": ["kind"],
            },
        ),
    ]
