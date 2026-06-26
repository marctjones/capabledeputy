"""Tests for the unified-TUI view-model (pure functions)."""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from capabledeputy.tui.console_model import (
    format_history_turn,
    format_session_history,
    format_turn,
    outcome_line,
    pending_approvals,
    status_lines,
)


def _render_item(item: str | object) -> str:
    if isinstance(item, str):
        return Text.from_markup(item).plain
    console = Console(width=120, record=True, force_terminal=False)
    console.print(item)
    return console.export_text().strip()


def _plain(lines: list[str | object]) -> str:
    """Markup-stripped join — assert on what the human reads, not the
    Rich tag soup."""
    return "\n".join(_render_item(line) for line in lines)


def test_outcome_line_allow_and_deny() -> None:
    allow = Text.from_markup(
        outcome_line(
            {
                "decision": "allow",
                "tool_name": "inbox.list",
                "labels_added": ["untrusted.external"],
            },
        )
    ).plain
    assert "✓ allow" in allow
    assert "inbox.list" in allow
    assert "+untrusted.external" in allow

    deny = Text.from_markup(
        outcome_line(
            {"decision": "deny", "tool_name": "email.send", "rule": "untrusted-meets-egress"},
        )
    ).plain
    assert "✗ deny" in deny
    assert "rule=untrusted-meets-egress" in deny


def test_format_turn_includes_reply_and_recovery_hint() -> None:
    result = {
        "content": "I can't do that.",
        "iterations": 2,
        "finish_reason": "stop",
        "tool_outcomes": [
            {
                "decision": "deny",
                "tool_name": "email.send",
                "rule": "untrusted-meets-egress",
                "reason": "rule fired",
            },
        ],
    }
    lines = format_turn(result)
    blob = _plain([line for line in lines if isinstance(line, str)])
    assert "agent" in blob
    from capabledeputy.cli.markdown_media import render_trusted_markdown

    md_console = Console(width=80, record=True, force_terminal=False)
    md_console.print(render_trusted_markdown(result["content"]))
    assert "I can't do that." in md_console.export_text()
    assert "✗ deny" in blob
    assert "rule fired" in blob
    assert "↳ recover:" in blob  # deterministic hint surfaced


def test_pending_approvals_extracts_ids() -> None:
    result = {
        "tool_outcomes": [
            {"decision": "allow", "tool_name": "memory.read"},
            {"decision": "require_approval", "tool_name": "purchase.queue", "approval_id": 7},
            {"decision": "require_approval", "tool_name": "x", "approval_id": None},
        ],
    }
    assert pending_approvals(result) == [7]


def test_pending_approvals_empty_when_none() -> None:
    assert pending_approvals({"tool_outcomes": [{"decision": "allow"}]}) == []


def test_status_lines_show_compartment_and_caps() -> None:
    session = {
        "label_set": ["untrusted.external"],
        "used_kinds": ["READ_FS"],
        "capability_set": [
            {"kind": "SEND_EMAIL", "pattern": "*@x.com"},
            {
                "kind": "QUEUE_PURCHASE",
                "pattern": "amazon",
                "rate_limit": {"max_uses": 3, "window_seconds": 60},
            },
        ],
    }
    lines = status_lines(session)
    blob = _plain(lines)
    assert "TAINTED" in blob
    assert "untrusted.external" in blob
    assert "used:" in blob and "READ_FS" in blob
    assert "capabilities (2)" in blob
    assert "SEND_EMAIL" in blob
    assert "rate 3/60s" in blob  # constraint surfaced via presentation


def test_format_session_history_renders_user_and_agent_turns() -> None:
    lines = format_session_history(
        [
            {"role": "user", "content": "hello"},
            {"role": "agent", "content": "hi"},
        ],
    )
    blob = _plain(lines)
    assert "user" in blob and "hello" in blob
    assert "agent" in blob and "hi" in blob


def test_format_history_turn_preserves_multiline_content() -> None:
    lines = format_history_turn({"role": "agent", "content": "line one\nline two"})
    blob = _plain(lines)
    assert "line one" in blob and "line two" in blob


def test_status_lines_clean_session_no_caps() -> None:
    lines = status_lines({"label_set": [], "capability_set": []})
    blob = _plain(lines)
    assert "clean" in blob
    assert "capabilities (0)" in blob
    assert "none" in blob
