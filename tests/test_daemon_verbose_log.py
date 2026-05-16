"""Tests for the daemon verbose-log helpers.

Full end-to-end verbose-log tests would need to spin up the daemon
and assert stderr output. We test the pure helpers (param/result
summarisers) instead, which are the part most likely to break.
"""

from __future__ import annotations

from capabledeputy.daemon.verbose_log import _summarize_params, _summarize_result


def test_session_send_summary_includes_short_session_and_message() -> None:
    out = _summarize_params(
        "session.send",
        {"session_id": "2092fcfe-aaaa-bbbb-cccc-dddddddddddd", "message": "hi"},
    )
    assert "sid=2092fcfe" in out
    assert "'hi'" in out


def test_session_send_summary_truncates_long_message() -> None:
    msg = "x" * 100
    out = _summarize_params(
        "session.send",
        {"session_id": "abcd0000-0000-0000-0000-000000000000", "message": msg},
    )
    assert "…" in out


def test_approval_summary_shows_id_and_action() -> None:
    out = _summarize_params(
        "approval.approve",
        {"id": 7, "action": "SEND_EMAIL"},
    )
    assert "id=7" in out
    assert "action=SEND_EMAIL" in out


def test_demo_start_summary_shows_name() -> None:
    out = _summarize_params("demo.start", {"name": "daily-briefing"})
    assert out == "name=daily-briefing"


def test_session_send_result_summary_counts_outcomes() -> None:
    result = {
        "iterations": 2,
        "tool_outcomes": [
            {"decision": "allow"},
            {"decision": "deny", "rule": "x"},
            {"decision": "require_approval"},
        ],
    }
    out = _summarize_result("session.send", result)
    assert "iters=2" in out
    assert "outcomes=3" in out
    assert "deny=1" in out
    assert "approval=1" in out


def test_approval_approve_result_summary_shows_dispatch() -> None:
    result = {
        "approval": {"id": 5},
        "executed_in_session": "abcd1234-0000-0000-0000-000000000000",
        "dispatch": {"decision": "allow"},
    }
    out = _summarize_result("approval.approve", result)
    assert "id=5" in out
    assert "dispatched=abcd1234" in out
    assert "dispatch=allow" in out


def test_empty_params_returns_empty_string() -> None:
    assert _summarize_params("ping", {}) == ""


def test_unknown_method_uses_key_only_fallback() -> None:
    out = _summarize_params("custom.weird", {"foo": "secret", "bar": 42})
    # Keys only — no values, to avoid leaking sensitive payloads.
    assert out == "bar, foo"
    assert "secret" not in out
    assert "42" not in out
