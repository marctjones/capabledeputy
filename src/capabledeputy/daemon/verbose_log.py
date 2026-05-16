"""Verbose RPC logger for the daemon.

When `capdep daemon start --verbose` is set, every JSON-RPC request
that hits the daemon prints one line to stdout: timestamp, method
name, a short param summary, result summary, and duration. The
intent is "I can see the agent and REPL talking to me" — not a full
audit log (`capdep watch` is for that).

Cache-y methods (REPL completer + watch consumers — `audit.tail`,
`session.list`, `approval.list`, `extract.schemas`,
`extract.inbox_ids`) are rendered dim so they don't dominate. Errors
and slow calls (>500ms) are highlighted.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from rich.console import Console

_NOISY_METHODS: frozenset[str] = frozenset(
    {
        "audit.tail",
        "session.list",
        "approval.list",
        "extract.schemas",
        "extract.inbox_ids",
        "ping",
    },
)


class VerboseLogger:
    def __init__(self) -> None:
        # stderr keeps daemon stdout clean for pipes that consume it,
        # and ensures the log doesn't tangle with the rich progress
        # bar we might add later.
        self._console = Console(stderr=True)

    def log_ok(
        self,
        method: str,
        params: dict[str, Any],
        result: Any,
        elapsed_ms: float,
    ) -> None:
        ts = _now()
        param_brief = _summarize_params(method, params)
        result_brief = _summarize_result(method, result)
        slow = elapsed_ms > 500
        time_color = "yellow" if slow else "green"
        noisy = method in _NOISY_METHODS
        style = "dim" if noisy else ""
        prefix = f"[dim]{ts}[/dim] [{time_color}]{elapsed_ms:6.0f}ms[/{time_color}]"
        body = f"{method}"
        if param_brief:
            body += f"  {param_brief}"
        if result_brief:
            body += f"  → {result_brief}"
        line = f"{prefix} {body}"
        if style:
            line = f"[{style}]{line}[/{style}]"
        self._console.print(line)

    def log_error(
        self,
        method: str,
        params: dict[str, Any],
        exc: BaseException,
        elapsed_ms: float,
    ) -> None:
        ts = _now()
        param_brief = _summarize_params(method, params)
        self._console.print(
            f"[dim]{ts}[/dim] [red]{elapsed_ms:6.0f}ms[/red] "
            f"{method}  {param_brief}  [red]ERROR: {exc}[/red]",
        )


def _now() -> str:
    now = datetime.now()
    return now.strftime("%H:%M:%S.") + f"{now.microsecond // 1000:03d}"


def _summarize_params(method: str, params: dict[str, Any]) -> str:
    """Pick out the most useful fields per method. Falls back to a
    one-line repr of the full dict (truncated)."""
    if not params:
        return ""

    # Per-method extraction for the verbose-but-readable common cases.
    if method == "session.send":
        sid = str(params.get("session_id", ""))[:8]
        msg = str(params.get("message", ""))
        if len(msg) > 50:
            msg = msg[:47] + "…"
        return f"sid={sid} msg={msg!r}"

    if method.startswith("session.") or method == "tool.call":
        sid = str(params.get("session_id", ""))[:8]
        extras = []
        if "tool" in params:
            extras.append(f"tool={params['tool']}")
        if "intent" in params:
            intent = str(params["intent"])[:30]
            extras.append(f"intent={intent!r}")
        if "labels" in params:
            extras.append(f"labels={params['labels']}")
        head = f"sid={sid}" if sid else ""
        tail = " ".join(extras)
        return f"{head} {tail}".strip()

    if method.startswith("approval."):
        bits = []
        if "id" in params:
            bits.append(f"id={params['id']}")
        if "action" in params:
            bits.append(f"action={params['action']}")
        if "target" in params:
            bits.append(f"target={params['target']}")
        return " ".join(bits)

    if method == "demo.start":
        return f"name={params.get('name')}"

    if method == "extract.inbox_message":
        return f"msg={params.get('message_id')} schema={params.get('schema')}"

    # Generic fallback: keys only, no values (avoids leaking payloads).
    return ", ".join(sorted(params.keys()))


def _summarize_result(method: str, result: Any) -> str:
    if not isinstance(result, dict):
        return ""

    if method == "session.send":
        outcomes = result.get("tool_outcomes", [])
        n = len(outcomes)
        denies = sum(1 for o in outcomes if o.get("decision") == "deny")
        approvals = sum(
            1 for o in outcomes if o.get("decision") == "require_approval"
        )
        bits = [f"iters={result.get('iterations')}", f"outcomes={n}"]
        if denies:
            bits.append(f"[red]deny={denies}[/red]")
        if approvals:
            bits.append(f"[yellow]approval={approvals}[/yellow]")
        return " ".join(bits)

    if method == "approval.approve":
        approved = result.get("approval", {})
        bits = [f"id={approved.get('id')}"]
        if result.get("executed_in_session"):
            bits.append(f"dispatched={result['executed_in_session'][:8]}")
        dispatch = result.get("dispatch") or {}
        if dispatch.get("decision"):
            bits.append(f"dispatch={dispatch['decision']}")
        return " ".join(bits)

    if method.startswith("session."):
        if "id" in result:
            return f"id={result['id'][:8]}"
        if "sessions" in result:
            return f"n={len(result['sessions'])}"

    if method == "approval.list":
        return f"n={len(result.get('approvals', []))}"

    if method == "audit.list" or method == "audit.tail":
        return f"n={len(result.get('events', []))}"

    return ""
