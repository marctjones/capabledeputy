"""Tests for the REPL presentation helpers.

These are pure functions (palette, compartment summary, toolbar
rendering) — the interactive loop itself is exercised by hand. The
toolbar render is tested against a fake CompletionCache so we can
assert the color-coded compartment band without a daemon.
"""

from __future__ import annotations

from typing import Any, cast

from prompt_toolkit.formatted_text import to_plain_text

from capabledeputy.cli.chat import (
    _DENY_RECOVERY,
    _compartment_summary,
    _label_rich_style,
    _make_bottom_toolbar,
    _render_labels_rich,
    _render_outcomes_table,
    _summarize_tool_args,
    _summarize_tool_output,
    _tool_icon,
    console,
)
from capabledeputy.cli.completer import CompletionCache


def test_label_style_palette() -> None:
    assert _label_rich_style("untrusted.external") == "bold red"
    assert _label_rich_style("confidential.health") == "yellow"
    assert _label_rich_style("trusted.user_direct") == "green"
    assert _label_rich_style("egress.email") == "magenta"
    assert _label_rich_style("something.else") == "white"


def test_render_labels_empty_is_clean() -> None:
    assert _render_labels_rich([]) == "[green]clean[/green]"


def test_render_labels_sorted_and_styled() -> None:
    out = _render_labels_rich(["untrusted.external", "confidential.health"])
    # sorted: confidential first
    assert out.index("confidential.health") < out.index("untrusted.external")
    assert "[bold red]untrusted.external[/bold red]" in out


def test_compartment_summary_precedence() -> None:
    assert _compartment_summary([]) == ("clean", "green")
    assert _compartment_summary(["trusted.user_direct"]) == ("clean", "green")
    assert _compartment_summary(["confidential.financial"]) == (
        "confidential",
        "yellow",
    )
    # untrusted dominates even with confidential present
    assert _compartment_summary(
        ["confidential.financial", "untrusted.external"],
    ) == ("TAINTED", "bold red")


def test_deny_recovery_covers_the_hard_deny_rules() -> None:
    for rule in (
        "untrusted-meets-egress",
        "health-meets-egress",
        "financial-meets-email",
        "capability-revoked-by-prior-use",
    ):
        assert rule in _DENY_RECOVERY
        assert _DENY_RECOVERY[rule]


class _FakeCache:
    def __init__(self, sessions: list[dict[str, Any]], approval_ids: list[int]):
        self.sessions = sessions
        self.approval_ids = approval_ids


def _toolbar_text(cache: Any, sid: str) -> str:
    focus = {"id": sid, "label": sid[:8]}
    render = _make_bottom_toolbar(cache, focus)
    return to_plain_text(render())


def test_toolbar_syncing_when_session_not_in_cache() -> None:
    txt = _toolbar_text(_FakeCache([], []), "abcd1234-0000-0000-0000-x")
    assert "abcd1234" in txt
    assert "syncing" in txt


def test_toolbar_clean_session() -> None:
    sid = "abcd1234-0000-0000-0000-000000000001"
    cache = _FakeCache(
        [{"id": sid, "label_set": [], "capability_set": [1, 2, 3]}],
        [],
    )
    txt = _toolbar_text(cache, sid)
    assert "clean" in txt
    assert "caps 3" in txt
    assert "pending" not in txt


def test_toolbar_tainted_with_pending() -> None:
    sid = "abcd1234-0000-0000-0000-000000000002"
    cache = _FakeCache(
        [
            {
                "id": sid,
                "label_set": ["untrusted.external", "confidential.personal"],
                "capability_set": [1],
            },
        ],
        [7, 9],
    )
    txt = _toolbar_text(cache, sid)
    assert "TAINTED" in txt
    assert "untrusted.external" in txt
    assert "2 pending" in txt


def _capture_outcomes(outcomes: list[dict[str, Any]]) -> str:
    with console.capture() as cap:
        _render_outcomes_table(outcomes)
    return cap.get()


def test_real_deny_shows_recovery_hint() -> None:
    out = _capture_outcomes(
        [
            {
                "decision": "deny",
                "tool_name": "email.send",
                "rule": "untrusted-meets-egress",
                "reason": "rule fired on labels",
            },
        ],
    )
    assert "recover" in out
    assert "/spawn" in out or "/extract" in out


def test_preview_deny_surfaces_hint_even_without_deny_row() -> None:
    """The agent preview-checks and skips the real call, so the only
    outcome is `allow policy.preview` whose OUTPUT says deny. The
    recovery hint must still appear."""
    out = _capture_outcomes(
        [
            {
                "decision": "allow",
                "tool_name": "policy.preview",
                "output": {
                    "decision": "deny",
                    "rule": "untrusted-meets-egress",
                    "reason": "would fire",
                },
            },
        ],
    )
    assert "preview" in out
    assert "would DENY" in out
    assert "recover" in out


def test_preview_allow_shows_no_hint() -> None:
    out = _capture_outcomes(
        [
            {
                "decision": "allow",
                "tool_name": "policy.preview",
                "output": {"decision": "allow", "rule": None},
            },
        ],
    )
    assert "recover" not in out
    assert "would DENY" not in out


# --- Time-bounded capability rendering (feature 001, US3) ----------------

from datetime import UTC, datetime, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402

from capabledeputy.cli.chat import _expiry_marker  # noqa: E402


def _cap(expires_at: str | None) -> dict[str, Any]:
    return {"kind": "READ_FS", "pattern": "*", "expires_at": expires_at}


def test_expiry_marker_none_is_empty() -> None:
    assert _expiry_marker(_cap(None)) == ""


def test_expiry_marker_future_shows_remaining() -> None:
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    deadline = (now + timedelta(seconds=90)).isoformat()
    out = _expiry_marker(_cap(deadline), now=now)
    assert "expires in" in out
    assert "90s" in out


def test_expiry_marker_past_shows_expired() -> None:
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    deadline = (now - timedelta(seconds=1)).isoformat()
    out = _expiry_marker(_cap(deadline), now=now)
    assert "expired" in out


def test_status_render_annotates_time_bounded_cap(
    monkeypatch: Any,
) -> None:
    from capabledeputy.cli import chat

    future = (datetime.now(UTC) + timedelta(seconds=120)).isoformat()
    monkeypatch.setattr(
        chat,
        "_call",
        lambda m, p=None: {
            "label_set": [],
            "used_kinds": [],
            "capability_set": [
                {"kind": "READ_FS", "pattern": "*", "expires_at": future},
                {"kind": "SEND_EMAIL", "pattern": "*", "expires_at": None},
            ],
        },
    )
    with chat.console.capture() as cap:
        chat._handle_status("sid", only="caps")
    out = cap.get()
    assert "expires in" in out  # the time-bounded one annotated
    # the non-expiring one has no marker on its line
    assert "SEND_EMAIL pattern=*" in out


def test_session_show_annotates_time_bounded_cap(monkeypatch: Any) -> None:
    from capabledeputy.cli import chat

    past = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
    monkeypatch.setattr(
        chat,
        "_call",
        lambda m, p=None: {
            "id": "abcd1234-...",
            "status": "active",
            "capability_set": [
                {"kind": "READ_FS", "pattern": "*", "expires_at": past},
            ],
        },
    )
    with chat.console.capture() as cap:
        chat._handle_session_show("abcd1234")
    assert "expired" in cap.get()


def test_toolbar_shows_time_bound_marker() -> None:
    sid = "abcd1234-0000-0000-0000-000000000099"
    future = (datetime.now(UTC) + timedelta(seconds=300)).isoformat()
    cache = _FakeCache(
        [
            {
                "id": sid,
                "label_set": [],
                "capability_set": [
                    {"kind": "READ_FS", "pattern": "*", "expires_at": future},
                    {"kind": "SEND_EMAIL", "pattern": "*", "expires_at": None},
                ],
            },
        ],
        [],
    )
    txt = _toolbar_text(cache, sid)
    # one of two caps is time-bounded → toolbar surfaces it
    assert "ttl" in txt or "⏳" in txt


# --- Tool-card formatting (Issue #?? style pass) -------------------------


def test_tool_icon_known_prefixes() -> None:
    assert _tool_icon("gws.gmail_messages_list") == "📧"
    assert _tool_icon("gws.drive_files_list") == "📂"
    assert _tool_icon("fs.read") == "📁"
    assert _tool_icon("fetch.get") == "🌐"
    assert _tool_icon("memory.write") == "🧠"


def test_tool_icon_fallback() -> None:
    """Unknown tools get the generic wrench so the visual cue
    'a tool ran here' still lands even when we don't know the category."""
    assert _tool_icon("unknown.tool") == "🔧"
    assert _tool_icon(None) == "🔧"
    assert _tool_icon("") == "🔧"


def test_summarize_tool_args_prefers_meaningful_keys() -> None:
    """`q`/`path`/`id` win over noisy keys when both are present."""
    out = _summarize_tool_args({"q": "after:2026-05-22", "userId": "me", "maxResults": 20})
    assert "q=after:2026-05-22" in out
    # userId is not in the priority list, so it should NOT lead
    assert out.startswith("q=")


def test_summarize_tool_args_truncates_long_values() -> None:
    out = _summarize_tool_args({"path": "/very/long/path/" + "x" * 50})
    assert "…" in out
    assert len(out) < 80  # capped, not multi-line


def test_summarize_tool_args_empty() -> None:
    assert _summarize_tool_args(None) == ""
    assert _summarize_tool_args({}) == ""
    assert _summarize_tool_args("not a dict") == ""


def test_summarize_tool_output_deny() -> None:
    out = _summarize_tool_output({"decision": "deny", "rule": "no-egress"})
    assert "DENIED by no-egress" in out


def test_summarize_tool_output_approval() -> None:
    out = _summarize_tool_output({"decision": "require_approval"})
    assert "queued for approval" in out


def test_summarize_tool_output_error() -> None:
    out = _summarize_tool_output({"decision": "allow", "error": "timeout"})
    assert "timeout" in out
    assert "✗" in out


def test_summarize_tool_output_text_byte_count() -> None:
    out = _summarize_tool_output(
        {"decision": "allow", "output": {"text": "x" * 1234}},
    )
    assert "1,234 chars" in out


def test_summarize_tool_output_list_count() -> None:
    out = _summarize_tool_output(
        {"decision": "allow", "output": {"messages": [1, 2, 3, 4, 5]}},
    )
    assert "5 messages" in out


def test_summarize_tool_output_truncation_marker() -> None:
    """When upstream adapter truncated the response, surface the
    ORIGINAL size with a (truncated) marker so the operator knows the
    LLM saw less than was available."""
    out = _summarize_tool_output(
        {
            "decision": "allow",
            "output": {"text": "x" * 32000, "truncated": True, "original_size_bytes": 105_000},
        },
    )
    assert "105,000 bytes" in out
    assert "truncated" in out


def test_render_outcome_includes_icon_and_args() -> None:
    """End-to-end: the rendered card has the email icon, the tool
    name, the args summary, and the result preview — all on one line."""
    with console.capture() as cap:
        _render_outcomes_table(
            [
                {
                    "decision": "allow",
                    "tool_name": "gws.gmail_messages_list",
                    "tool_args": {"q": "after:2026-05-22", "maxResults": 20},
                    "output": {"messages": [{"id": "a"}, {"id": "b"}]},
                },
            ],
        )
    out = cap.get()
    assert "📧" in out
    assert "gws.gmail_messages_list" in out
    assert "after:2026-05-22" in out
    assert "2 messages" in out


def test_render_outcome_deny_uses_warning_icon() -> None:
    """Denials swap the tool icon for `⊘` so they pop visually in a
    multi-call turn."""
    with console.capture() as cap:
        _render_outcomes_table(
            [
                {
                    "decision": "deny",
                    "tool_name": "email.send",
                    "tool_args": {"to": "x@y.com"},
                    "rule": "no-egress",
                },
            ],
        )
    out = cap.get()
    assert "⊘" in out
    assert "DENIED by no-egress" in out


# --- Token-counter segment in the bottom toolbar -------------------------


from capabledeputy.cli.chat import _toolbar_context_segment  # noqa: E402


def test_toolbar_context_segment_hidden_before_first_turn() -> None:
    """No turn has fired yet → don't pretend we know the context size."""
    assert _toolbar_context_segment(None, None) == ""
    assert _toolbar_context_segment(0, None) == ""
    assert _toolbar_context_segment(None, 200_000) == ""


def test_toolbar_context_segment_low_usage_is_gray() -> None:
    seg = _toolbar_context_segment(12_000, 200_000)
    assert "ansigray" in seg
    assert "6%" in seg
    assert "12k/200k" in seg


def test_toolbar_context_segment_warn_threshold_is_yellow() -> None:
    seg = _toolbar_context_segment(140_000, 200_000)
    assert "ansiyellow" in seg
    assert "70%" in seg


def test_toolbar_context_segment_cliff_is_red() -> None:
    seg = _toolbar_context_segment(180_000, 200_000)
    assert "ansired" in seg
    assert "90%" in seg


def test_toolbar_picks_up_context_from_state() -> None:
    """End-to-end via _make_bottom_toolbar: when state carries
    context_tokens + context_window, the toolbar string contains the
    `ctx N/M P%` segment. Pre-turn (no state keys) it's absent."""
    sid = "abcd1234-0000-0000-0000-00000000ccc1"
    cache = _FakeCache(
        [{"id": sid, "label_set": [], "capability_set": []}],
        [],
    )
    focus = {"id": sid, "label": sid[:8]}

    # Without context state — no segment
    render_off = _make_bottom_toolbar(cast(CompletionCache, cache), focus, state={})
    assert "ctx " not in to_plain_text(render_off())

    # With context state — segment present
    render_on = _make_bottom_toolbar(
        cast(CompletionCache, cache),
        focus,
        state={"context_tokens": 50_000, "context_window": 200_000},
    )
    out = to_plain_text(render_on())
    assert "ctx" in out
    assert "50k/200k" in out
    assert "25%" in out


# --- Per-tool result formatters ------------------------------------------


def test_gmail_get_formatter_extracts_subject_and_from() -> None:
    """The 30k-char gmail message renders as `From: X · "Subject"`,
    not a byte count. Subject is the actually-useful preview."""
    import json

    text = json.dumps(
        {
            "payload": {
                "headers": [
                    {"name": "From", "value": '"The Capitalist" <news@substack.com>'},
                    {"name": "Subject", "value": "Legendary automaker tanks"},
                    {"name": "To", "value": "marc@example.com"},
                ],
            },
            "snippet": "...",
        },
    )
    out = _summarize_tool_output(
        {
            "decision": "allow",
            "tool_name": "gws.gmail_messages_get",
            "output": {"text": text},
        },
    )
    assert "The Capitalist" in out
    assert "Legendary automaker tanks" in out
    # Old generic preview would have shown "X chars" — the new one
    # should NOT, because we matched the per-tool formatter.
    assert "chars" not in out


def test_gmail_get_formatter_handles_no_subject_gracefully() -> None:
    """An email with no Subject header still renders something
    useful — `(no subject)` placeholder rather than crashing."""
    import json

    text = json.dumps(
        {
            "payload": {
                "headers": [
                    {"name": "From", "value": "x@y.com"},
                ],
            },
        },
    )
    out = _summarize_tool_output(
        {
            "decision": "allow",
            "tool_name": "gws.gmail_messages_get",
            "output": {"text": text},
        },
    )
    assert "no subject" in out


def test_gmail_list_formatter_counts_messages() -> None:
    import json

    text = json.dumps(
        {"messages": [{"id": f"id-{i}", "threadId": f"t-{i}"} for i in range(5)]},
    )
    out = _summarize_tool_output(
        {
            "decision": "allow",
            "tool_name": "gws.gmail_messages_list",
            "output": {"text": text},
        },
    )
    assert "5 messages" in out


def test_drive_list_formatter_includes_filenames() -> None:
    import json

    text = json.dumps(
        {"files": [{"name": "report.pdf"}, {"name": "notes.md"}, {"name": "slides.key"}]},
    )
    out = _summarize_tool_output(
        {
            "decision": "allow",
            "tool_name": "gws.drive_files_list",
            "output": {"text": text},
        },
    )
    assert "3 files" in out
    assert "report.pdf" in out
    assert "notes.md" in out


def test_drive_list_formatter_truncates_with_ellipsis() -> None:
    import json

    text = json.dumps({"files": [{"name": f"f{i}.txt"} for i in range(10)]})
    out = _summarize_tool_output(
        {
            "decision": "allow",
            "tool_name": "gws.drive_files_list",
            "output": {"text": text},
        },
    )
    assert "10 files" in out
    assert "…" in out  # truncation marker — only first 3 shown


def test_fs_read_formatter_reports_lines_and_bytes() -> None:
    content = "line1\nline2\nline3\n"
    out = _summarize_tool_output(
        {
            "decision": "allow",
            "tool_name": "fs.read",
            "output": {"content": content, "path": "/x"},
        },
    )
    assert "3 lines" in out
    assert "bytes" in out


def test_specific_formatter_falls_through_on_bad_json() -> None:
    """If the upstream returned malformed JSON in text, the
    formatter returns None and the generic byte-count fallback
    fires — never a render crash."""
    out = _summarize_tool_output(
        {
            "decision": "allow",
            "tool_name": "gws.gmail_messages_get",
            "output": {"text": "not even close to JSON"},
        },
    )
    # generic fallback engages
    assert "chars" in out


def test_formatter_carries_truncation_marker() -> None:
    """When the upstream output was capped (truncated=True), the
    per-tool preview still wins but appends the original-size
    marker so the operator knows the LLM saw less than was
    available."""
    import json

    text = json.dumps(
        {
            "payload": {
                "headers": [
                    {"name": "From", "value": "x@y.com"},
                    {"name": "Subject", "value": "Hello"},
                ],
            },
        },
    )
    out = _summarize_tool_output(
        {
            "decision": "allow",
            "tool_name": "gws.gmail_messages_get",
            "output": {
                "text": text,
                "truncated": True,
                "original_size_bytes": 105_000,
            },
        },
    )
    assert "Hello" in out
    assert "105,000" in out
    assert "truncated" in out


# --- Stale-after-5-minutes badge for the ctx segment ---------------------


def test_ctx_segment_not_stale_when_recent() -> None:
    """Just-measured ctx → no stale marker, normal coloring."""
    now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC)
    measured_at = now - timedelta(seconds=30)
    seg = _toolbar_context_segment(50_000, 200_000, measured_at, now=now)
    assert "stale" not in seg
    assert "25%" in seg


def test_ctx_segment_stale_after_threshold() -> None:
    """6-minute-old reading → `(stale)` suffix + dim gray regardless
    of percentage. A stale 90% reading shouldn't trigger red panic."""
    now = datetime(2026, 5, 26, 12, 6, 0, tzinfo=UTC)
    measured_at = now - timedelta(minutes=6)
    seg = _toolbar_context_segment(180_000, 200_000, measured_at, now=now)
    assert "(stale)" in seg
    assert "ansigray" in seg
    # The cliff color should NOT fire when stale
    assert "ansired" not in seg


def test_ctx_segment_no_timestamp_uses_normal_coloring() -> None:
    """Back-compat: when `measured_at` is None (e.g. an older
    audit-less code path), the segment renders normally with
    threshold coloring — no stale marker."""
    seg = _toolbar_context_segment(180_000, 200_000, None)
    assert "stale" not in seg
    assert "ansired" in seg  # 90% triggers red, no staleness override


# --- Token spend indicator: session + month-to-date ----------------------


from capabledeputy.cli.chat import (  # noqa: E402
    _read_mtd_usage,
    _toolbar_usage_segment,
)


def test_usage_segment_hidden_before_first_turn() -> None:
    """All four counters at zero → segment collapses. A brand-new
    session with no audit history should not show `tok 0/0 · mtd 0/0`
    in the toolbar; that's just noise until something actually fires."""
    assert _toolbar_usage_segment(0, 0, 0, 0) == ""
    assert _toolbar_usage_segment(None, None, None, None) == ""


def test_usage_segment_shows_session_and_mtd() -> None:
    """Session = (1.2k, 340), mtd = (87k, 12k) → both columns render
    with the up/down arrows and compact `k` suffixes."""
    seg = _toolbar_usage_segment(1234, 340, 87_000, 12_500)
    assert "tok 1.2k↑/340↓" in seg
    assert "mtd 87k↑/12k↓" in seg
    assert "ansigray" in seg


def test_usage_segment_renders_with_zero_session_when_mtd_present() -> None:
    """Fresh REPL launched after prior usage this month — session
    counters are zero but mtd is non-zero, so the segment should
    still render rather than collapse."""
    seg = _toolbar_usage_segment(0, 0, 5_000, 1_200)
    assert "tok 0↑/0↓" in seg
    assert "mtd 5.0k↑/1.2k↓" in seg


def test_read_mtd_usage_missing_file_returns_zeros(tmp_path: Path) -> None:
    """No audit log yet → don't blow up REPL startup; return zeros so
    the toolbar just shows session-only counts."""
    assert _read_mtd_usage(tmp_path / "nope.jsonl") == (0, 0)
    assert _read_mtd_usage(None) == (0, 0)
    assert _read_mtd_usage("") == (0, 0)


def test_read_mtd_usage_sums_only_current_month(tmp_path: Path) -> None:
    """Events from prior months are excluded — operator-facing
    metric should track the calendar month, not a rolling window.
    Three events seeded: one in the prior month (skipped), two in
    the current month (summed)."""
    import json

    audit = tmp_path / "audit.jsonl"
    now = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)
    prior = datetime(2026, 4, 30, 23, 59, 59, tzinfo=UTC)
    in_month_a = datetime(2026, 5, 1, 0, 0, 1, tzinfo=UTC)
    in_month_b = datetime(2026, 5, 10, 9, 0, 0, tzinfo=UTC)

    def _event(ts: datetime, prompt: int, completion: int) -> str:
        return json.dumps(
            {
                "event_type": "llm.response_received",
                "timestamp": ts.isoformat(),
                "payload": {
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                },
            },
        )

    audit.write_text(
        "\n".join(
            [
                _event(prior, 999, 888),  # skipped — prior month
                _event(in_month_a, 1000, 200),
                _event(in_month_b, 500, 100),
            ],
        )
        + "\n",
    )
    p, c = _read_mtd_usage(audit, now=now)
    assert p == 1500
    assert c == 300


def test_read_mtd_usage_ignores_non_llm_response_events(tmp_path: Path) -> None:
    """Other event types in the same log must not bleed into the
    spend total — only `llm.response_received` carries the
    provider-reported usage."""
    import json

    audit = tmp_path / "audit.jsonl"
    now = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)
    in_month = datetime(2026, 5, 10, 9, 0, 0, tzinfo=UTC)

    audit.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event_type": "tool.dispatched",
                        "timestamp": in_month.isoformat(),
                        "payload": {"prompt_tokens": 9999},
                    },
                ),
                json.dumps(
                    {
                        "event_type": "llm.response_received",
                        "timestamp": in_month.isoformat(),
                        "payload": {
                            "prompt_tokens": 100,
                            "completion_tokens": 50,
                        },
                    },
                ),
            ],
        )
        + "\n",
    )
    assert _read_mtd_usage(audit, now=now) == (100, 50)


def test_read_mtd_usage_skips_malformed_lines(tmp_path: Path) -> None:
    """Partial writes / log rotation crumbs shouldn't crash startup —
    skip the bad line, sum the good ones."""
    import json

    audit = tmp_path / "audit.jsonl"
    now = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)
    in_month = datetime(2026, 5, 10, 9, 0, 0, tzinfo=UTC)

    audit.write_text(
        "\n".join(
            [
                "{not valid json",
                "",
                json.dumps(
                    {
                        "event_type": "llm.response_received",
                        "timestamp": in_month.isoformat(),
                        "payload": {
                            "prompt_tokens": 7,
                            "completion_tokens": 3,
                        },
                    },
                ),
                json.dumps(
                    {
                        "event_type": "llm.response_received",
                        "timestamp": "not-a-date",
                        "payload": {"prompt_tokens": 5, "completion_tokens": 5},
                    },
                ),
            ],
        )
        + "\n",
    )
    assert _read_mtd_usage(audit, now=now) == (7, 3)


def test_make_bottom_toolbar_renders_usage_when_state_populated() -> None:
    """End-to-end via _make_bottom_toolbar: when state carries the
    four usage counters, the `tok N↑/M↓ · mtd P↑/Q↓` segment appears
    in the toolbar string."""
    sid = "abcd1234-0000-0000-0000-00000000ddd2"
    cache = _FakeCache(
        [{"id": sid, "label_set": [], "capability_set": []}],
        [],
    )
    focus = {"id": sid, "label": sid[:8]}
    render = _make_bottom_toolbar(
        cast(CompletionCache, cache),
        focus,
        state={
            "session_prompt_tokens": 1200,
            "session_completion_tokens": 340,
            "mtd_prompt_tokens": 87_000,
            "mtd_completion_tokens": 12_500,
        },
    )
    out = to_plain_text(render())
    assert "tok 1.2k↑/340↓" in out
    assert "mtd 87k↑/12k↓" in out


# --- Cookbook P2.1 — /approvals sibling-group rendering ----------------


from capabledeputy.cli.chat import _render_approvals  # noqa: E402


def _make_request_dict(
    rid: int,
    *,
    action: str = "send_email",
    target: str = "spouse@example.com",
    payload: str = "hi",
    sibling_group_id: str | None = None,
) -> dict:
    """Minimal shape matching ApprovalRequest.to_dict() — only the
    fields _render_approvals reads."""
    return {
        "id": rid,
        "action": action,
        "target": target,
        "payload": payload,
        "sibling_group_id": sibling_group_id,
    }


def test_render_approvals_groups_siblings_under_one_header() -> None:
    """Three sibling requests render as a `Sibling group` table with
    an `approve-all (3)` paste link in the title."""
    gid = "11111111-1111-1111-1111-111111111111"
    approvals = [
        _make_request_dict(1, sibling_group_id=gid, payload="body 1"),
        _make_request_dict(2, sibling_group_id=gid, payload="body 2"),
        _make_request_dict(3, sibling_group_id=gid, payload="body 3"),
    ]
    from capabledeputy.cli import chat as _chat

    with _chat.console.capture() as cap:
        _render_approvals(approvals)
    out = cap.get()
    assert "Sibling group" in out
    # Title text may wrap across rich's panel width — assert both
    # markers appear, not that they're contiguous.
    assert "approve-all" in out
    assert "(3)" in out
    assert "send_email" in out
    assert "spouse@example.com" in out


def test_render_approvals_promotes_single_member_group_to_solo() -> None:
    """A group_id with one member is conceptually a solo — the
    renderer should drop the group framing and show it in the
    regular table to avoid pointless visual noise."""
    gid = "22222222-2222-2222-2222-222222222222"
    approvals = [
        _make_request_dict(7, sibling_group_id=gid),
    ]
    from capabledeputy.cli import chat as _chat

    with _chat.console.capture() as cap:
        _render_approvals(approvals)
    out = cap.get()
    assert "Sibling group" not in out
    assert "Pending approvals" in out


def test_render_approvals_mixes_grouped_and_solo() -> None:
    """Mixed view: one sibling group of two plus one solo. Both
    sections appear in the output."""
    gid = "33333333-3333-3333-3333-333333333333"
    approvals = [
        _make_request_dict(10, sibling_group_id=gid, payload="g1"),
        _make_request_dict(11, sibling_group_id=gid, payload="g2"),
        _make_request_dict(20, sibling_group_id=None, payload="solo"),
    ]
    from capabledeputy.cli import chat as _chat

    with _chat.console.capture() as cap:
        _render_approvals(approvals)
    out = cap.get()
    assert "Sibling group" in out
    assert "approve-all" in out
    assert "(2)" in out
    assert "Pending approvals (1 solo)" in out


def test_render_approvals_no_groups_no_header_change() -> None:
    """The pre-P2.1 happy path: a list of solo approvals renders
    exactly as before — `Pending approvals (N)` table, no group
    sections."""
    approvals = [
        _make_request_dict(1, sibling_group_id=None),
        _make_request_dict(2, sibling_group_id=None, target="boss@example.com"),
    ]
    from capabledeputy.cli import chat as _chat

    with _chat.console.capture() as cap:
        _render_approvals(approvals)
    out = cap.get()
    assert "Sibling group" not in out
    assert "Pending approvals" in out
