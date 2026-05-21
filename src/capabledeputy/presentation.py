"""Shared, pure presentation helpers for the REPL and the TUI.

The security model must read the same everywhere. These functions are
the single source of truth for: the label palette, the one-word
compartment health, and the capability-constraint summary (the whole
v0.7 family — one-shot, destructive, max-amount, expiry, rate limit,
prior-use revocation).

Everything here is a pure function over plain dicts/strings (the JSON
shapes the daemon already returns), returning Rich-console markup.
Textual `Static`/`DataTable` render Rich markup too, so one module
serves both surfaces. No imports from `cli`/`tui`/`daemon` — this is
a leaf module, freely importable by either UI.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def label_style(label: str) -> str:
    """Rich style for one information-flow label. One convention,
    reused in prompt, toolbar, trace, /status, and the TUI."""
    if label.startswith("untrusted."):
        return "bold red"
    if label.startswith("confidential."):
        return "yellow"
    if label.startswith("trusted."):
        return "green"
    if label.startswith("egress."):
        return "magenta"
    return "white"


def render_labels(labels: list[str]) -> str:
    """Color-coded ` · `-joined label set, or a green 'clean'."""
    if not labels:
        return "[green]clean[/green]"
    return " · ".join(f"[{label_style(lbl)}]{lbl}[/{label_style(lbl)}]" for lbl in sorted(labels))


def compartment_summary(labels: list[str]) -> tuple[str, str]:
    """(word, rich_style): one-word health of a session's compartment.
    Untrusted dominates confidential dominates clean."""
    if any(lbl.startswith("untrusted.") for lbl in labels):
        return "TAINTED", "bold red"
    if any(lbl.startswith("confidential.") for lbl in labels):
        return "confidential", "yellow"
    return "clean", "green"


def capability_markers(
    cap: dict[str, Any],
    *,
    now: datetime | None = None,
) -> list[str]:
    """Plain (no-markup) constraint tokens for a capability dict,
    covering the full v0.7 family. Order is stable so two renders of
    the same capability read identically. Empty list ⇒ an unconstrained
    capability (the pre-v0.7 default)."""
    ref = now or datetime.now(UTC)
    out: list[str] = []
    if cap.get("expiry") == "one_shot":
        out.append("one-shot")
    if cap.get("allows_destructive"):
        out.append("destructive")
    if cap.get("max_amount"):
        out.append(f"max={cap['max_amount']}")
    raw = cap.get("expires_at")
    if raw:
        deadline = datetime.fromisoformat(raw)
        if ref >= deadline:
            out.append("expired")
        else:
            out.append(f"expires {int((deadline - ref).total_seconds())}s")
    rl = cap.get("rate_limit")
    if rl:
        out.append(f"rate {rl['max_uses']}/{rl['window_seconds']}s")
    revoked = cap.get("revoked_by") or []
    if revoked:
        out.append("revoked-by:" + ",".join(revoked))
    return out


def _marker_style(token: str) -> str:
    if token == "expired" or token.startswith("revoked-by:"):
        return "red"
    return "yellow"


def capability_summary_rich(
    cap: dict[str, Any],
    *,
    now: datetime | None = None,
) -> str:
    """Rich-markup suffix for a capability line: ` (one-shot, expires
    42s, rate 3/60s)` etc., color-graded (red = already unusable).
    Empty string when unconstrained — callers append it verbatim."""
    markers = capability_markers(cap, now=now)
    if not markers:
        return ""
    body = ", ".join(f"[{_marker_style(m)}]{m}[/{_marker_style(m)}]" for m in markers)
    return f" ({body})"


def capability_line(
    cap: dict[str, Any],
    *,
    now: datetime | None = None,
) -> str:
    """One Rich-markup line for a capability: `KIND pattern=… (…)`."""
    return (
        f"[bold]{cap.get('kind', '?')}[/bold] "
        f"pattern={cap.get('pattern', '?')}"
        f"{capability_summary_rich(cap, now=now)}"
    )


# Policy rules whose denial has a known, deterministic operator
# recovery. Shared so the REPL hint and the TUI trace agree.
DENY_RECOVERY: dict[str, str] = {
    "untrusted-meets-egress": (
        "this session read untrusted content (e.g. inbox / fetched URL) and "
        "now wants to send it out — that's the bait-and-pivot pattern the "
        "engine blocks. Recovery: /spawn a fresh session with --intent "
        "'reply to <person>' and grant a one-shot SEND_EMAIL cap; or use "
        "/extract to declassify a specific fact first"
    ),
    "health-meets-egress": (
        "/spawn a clean session — health data cannot egress from a tainted session at all"
    ),
    "financial-meets-email": ("/spawn a clean session, or /extract a declassified summary first"),
    "capability-revoked-by-prior-use": (
        "/spawn a fresh session — this capability was revoked by a prior tool use in this one"
    ),
    "capability-expired": (
        "/grant a fresh capability (optionally with a longer --ttl) — the deadline has passed"
    ),
    "rate-limit-exceeded": (
        "wait for the rate window to slide, or /grant a capability with a higher --rate"
    ),
}
