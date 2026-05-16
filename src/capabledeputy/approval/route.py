"""Declarative approval routing for tools.

A tool that can return `REQUIRE_APPROVAL` declares HOW it should be
authorized once, at definition time, via an `ApprovalRoute`. The
`LabeledToolClient` resolves the route against the actual call args
into a ready-to-submit dict, which it attaches to the outcome. Every
client (REPL, TUI, MCP) then submits the approval from that resolved
dict — no client maintains a per-tool mapping table.

Three payload strategies cover the current tool set:

  - BODY_ARG: the approval payload is one named arg (e.g. an email
    body). `payload_arg` names it.
  - JSON_ARGS: the payload is the full args dict, JSON-encoded
    (e.g. a purchase: vendor + item + amount).
  - TOOL_ENVELOPE: the payload is `{"tool": <name>, "args": <args>}`,
    JSON-encoded — the generic destructive-op shape the
    EXECUTE_DESTRUCTIVE executor parses.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from capabledeputy.approval.model import ApprovalAction


class ApprovalPayloadKind(StrEnum):
    BODY_ARG = "body_arg"
    JSON_ARGS = "json_args"
    TOOL_ENVELOPE = "tool_envelope"


@dataclass(frozen=True)
class ApprovalRoute:
    action: ApprovalAction
    target_arg: str
    payload_kind: ApprovalPayloadKind
    payload_arg: str | None = None

    def resolve(
        self,
        tool_name: str,
        args: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        """Build the approval.submit params (minus `from_session`,
        which the submitting client adds since it knows the session)."""
        target = str(args.get(self.target_arg, ""))
        if self.payload_kind == ApprovalPayloadKind.BODY_ARG:
            if self.payload_arg is None:
                raise ValueError(
                    f"{tool_name}: BODY_ARG route needs payload_arg",
                )
            payload = str(args.get(self.payload_arg, ""))
        elif self.payload_kind == ApprovalPayloadKind.JSON_ARGS:
            payload = json.dumps(args)
        else:  # TOOL_ENVELOPE
            payload = json.dumps({"tool": tool_name, "args": args})
        return {
            "action": self.action.value,
            "target": target,
            "payload": payload,
            "justification": reason or f"agent-initiated {tool_name}",
        }
