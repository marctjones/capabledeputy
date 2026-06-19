"""Tests for the REPL completer.

The completer is sync and reads from a thread-safe cache. The cache
is fed by a daemon_call closure — we substitute a fake closure here
that returns canned responses, then assert what the completer yields
for various input strings.
"""

from __future__ import annotations

from typing import Any

from prompt_toolkit.document import Document

from capabledeputy.cli.completer import (
    SLASH_COMMANDS,
    CapDepCompleter,
    CompletionCache,
)


def _fake_daemon(state: dict[str, Any]):
    def call(method: str, params: dict[str, Any] | None = None) -> Any:
        if method == "session.list":
            return {"sessions": state.get("sessions", [])}
        if method == "approval.list":
            return {"approvals": state.get("approvals", [])}
        if method == "extract.schemas":
            return {"schemas": state.get("schemas", [])}
        if method == "extract.inbox_ids":
            return {"messages": state.get("inbox", [])}
        return {}

    return call


def _make_cache(state: dict[str, Any]) -> CompletionCache:
    """Build a cache pre-populated by one sync refresh; do NOT start
    the background thread (tests don't want it racing assertions)."""
    cache = CompletionCache(daemon_call=_fake_daemon(state))
    cache._refresh_once()  # sync prefetch only
    return cache


def _completions(text: str, state: dict[str, Any]) -> list[str]:
    cache = _make_cache(state)
    completer = CapDepCompleter(cache)
    doc = Document(text=text, cursor_position=len(text))
    return [c.text for c in completer.get_completions(doc, None)]


# ---- slash command completions ------------------------------------------


def test_slash_only_lists_all_commands() -> None:
    out = _completions("/", {})
    assert "/sessions" in out
    assert "/spawn" in out
    assert "/extract" in out
    assert len(out) == len(SLASH_COMMANDS)


def test_partial_command_completes() -> None:
    out = _completions("/sp", {})
    assert out == ["/spawn"]


def test_no_slash_means_no_completion() -> None:
    # Free text goes to the LLM — completer stays out of the way.
    out = _completions("what's on my plate today?", {})
    assert out == []


def test_unknown_command_completes_no_args() -> None:
    out = _completions("/nopecmd foo", {})
    assert out == []


# ---- session id completion ----------------------------------------------


def test_switch_lists_session_ids() -> None:
    state = {
        "sessions": [
            {"id": "abc12345-0000-0000-0000-000000000001", "intent": "alpha"},
            {"id": "def67890-0000-0000-0000-000000000002", "intent": "beta"},
        ],
    }
    out = _completions("/switch ", state)
    assert set(out) == {
        "abc12345-0000-0000-0000-000000000001",
        "def67890-0000-0000-0000-000000000002",
    }


def test_switch_filters_by_prefix() -> None:
    state = {
        "sessions": [
            {"id": "abc12345-0000-0000-0000-000000000001", "intent": "alpha"},
            {"id": "def67890-0000-0000-0000-000000000002", "intent": "beta"},
        ],
    }
    out = _completions("/switch abc", state)
    assert out == ["abc12345-0000-0000-0000-000000000001"]


def test_session_command_completes_session_ids() -> None:
    state = {
        "sessions": [{"id": "xyz12345-0000-0000-0000-000000000003", "intent": "g"}],
    }
    out = _completions("/session x", state)
    assert out == ["xyz12345-0000-0000-0000-000000000003"]


# ---- approval id completion ---------------------------------------------


def test_approve_lists_pending_ids() -> None:
    state = {"approvals": [{"id": 1}, {"id": 7}, {"id": 12}]}
    out = _completions("/approve ", state)
    assert set(out) == {"1", "7", "12"}


def test_approve_filters_by_prefix() -> None:
    state = {"approvals": [{"id": 1}, {"id": 7}, {"id": 12}]}
    out = _completions("/approve 1", state)
    assert set(out) == {"1", "12"}


# ---- capability kind completion -----------------------------------------


def test_grant_lists_capability_kinds() -> None:
    out = _completions("/grant ", {})
    assert "READ_FS" in out
    assert "SEND_EMAIL" in out
    assert "QUEUE_PURCHASE" in out


def test_grant_filters_kinds_case_insensitively() -> None:
    out = _completions("/grant se", {})
    assert out == ["SEND_EMAIL", "SEND_MESSAGE"]


def test_grant_flag_completion() -> None:
    out = _completions("/grant SEND_EMAIL alice@x.com --on", {})
    assert out == ["--one-shot"]


# ---- schema completion --------------------------------------------------


def test_extract_second_arg_completes_schemas() -> None:
    state = {"schemas": ["ContactInfo", "DailyBriefing", "EmailTriageItem"]}
    out = _completions("/extract m1 Con", state)
    assert out == ["ContactInfo"]


def test_extract_first_arg_completes_inbox_ids() -> None:
    state = {
        "inbox": [
            {"id": "m1", "sender": "a@x", "subject": "first"},
            {"id": "m2", "sender": "b@y", "subject": "second"},
        ],
        "schemas": ["ContactInfo"],
    }
    out = _completions("/extract ", state)
    assert set(out) == {"m1", "m2"}


def test_extract_first_arg_filters_by_prefix() -> None:
    state = {
        "inbox": [
            {"id": "m1", "sender": "a@x", "subject": "first"},
            {"id": "x42", "sender": "b@y", "subject": "second"},
        ],
    }
    out = _completions("/extract m", state)
    assert out == ["m1"]


# ---- approval action completion -----------------------------------------


def test_remember_completes_approval_action() -> None:
    out = _completions("/remember SE", {})
    assert out == ["SEND_EMAIL"]
