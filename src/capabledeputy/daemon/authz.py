"""Operator-only RPC method gate, enforced at the dispatch layer.

Several daemon RPC methods are documented as "operator-only" — meant to be
reachable only from the human's own trusted surfaces (chat REPL, direct CLI,
the macOS app), never from an AI agent's tool calls. Historically that
boundary was enforced only by convention: `capabledeputy.mcp_server.control`
curates which RPC methods it exposes as MCP tools, and methods left out of
that curation were assumed safe by omission. Two things make that
insufficient on its own:

1. The socket has no caller-identity check (see `daemon/server.py`), so any
   other same-OS-user process that knows the method name can call it
   directly — curation only hides the method from *one* client's tool list.
2. Curation itself has drifted: `session.set_enforcement`,
   `capability.revoke`, and `relationship_group.promote` are all documented
   operator-only ("the AI cannot invoke this", "Per Principle VI, the AI
   must NEVER reach this RPC") yet are exposed as MCP control-server tools
   today (`control.py`'s `_CONTROL_TOOL_SPECS`), reachable by any agent
   host through ordinary tool calls.

This module is the single declaration of which methods require the
operator token (see below), checked in `Daemon._handle_line` before
dispatch — independent of what any client happens to curate.

Threat model honesty: the operator token distinguishes "connected without
the token" from "connected with it" — nothing more. It stops (a) a random
same-user process that knows only the socket path and method name, and
(b) a curation mistake in an agent-facing MCP server from reaching these
methods. It does NOT stop an agent that has unmediated shell access as the
same OS user: such an agent can read the token file (0600, but same-uid)
or simply invoke the trusted `capdep` CLI directly, which reads it too.
Closing that gap requires a human-presence proof (e.g. the existing
`strong_auth` flow used for approvals) on the specific high-value
operations, not a transport-level credential — that is a separate,
product-level decision.
"""

from __future__ import annotations

# RPC method name -> why it's operator-only. Keep this list in sync with the
# handler docstrings it mirrors; it is intentionally the *only* place this
# gate is declared, so "operator-only" is one checkable fact instead of
# scattered convention.
OPERATOR_ONLY_METHODS: dict[str, str] = {
    # session_handlers.py — explicit "the AI cannot invoke this" / "the AI
    # cannot self-shadow" / destructive-widening grant path.
    "operator.grant_capability": "may set allows_destructive=True with no origin restriction",
    "capability.revoke": "operator-only per docstring; the AI cannot invoke this",
    "session.set_enforcement": "flips enforcement posture; the AI cannot self-shadow",
    "session.set_first_use_prompts": "operator-only per docstring (chat REPL /first-use)",
    # relationship_handlers.py — "The mutation surface is OPERATOR-ONLY by
    # construction ... Per Principle VI, the AI must NEVER reach this RPC."
    "relationship_group.add_member": "operator-only mutation per module docstring",
    "relationship_group.remove_member": "operator-only mutation per module docstring",
    "relationship_group.promote": "Principle VI: the AI must never reach this RPC",
    # server.py — handled inline before dispatch, but the same gap applies:
    # any connection (including an agent-facing MCP server) can currently
    # kill the daemon.
    "shutdown": "any connection can otherwise terminate the daemon process",
}


def requires_operator(method: str) -> bool:
    return method in OPERATOR_ONLY_METHODS
