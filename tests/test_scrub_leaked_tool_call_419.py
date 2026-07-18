"""#419 — a raw tool-call envelope must never reach the client as chat text.

Observed in GUI session 8c197384 (2026-07-18): a local model emitted
`{"tool_calls": [{"id": "1", "name": "…image.generate", "args": {…}}]}` as
literal output because the (truncated) stream did not dispatch. These pin the
scrubber that replaces such a leak with a clean message, while leaving ordinary
prose untouched.
"""

from __future__ import annotations

from capabledeputy.agent.loop import _scrub_leaked_tool_call_json

_CLEAN_MARKER = "didn't parse"


def test_bare_envelope_is_scrubbed() -> None:
    leaked = '{"tool_calls": [{"id": "1", "name": "image.generate", "args": {"prompt": "x"}}]}'
    out = _scrub_leaked_tool_call_json(leaked)
    assert "tool_calls" not in out
    assert _CLEAN_MARKER in out


def test_truncated_envelope_is_scrubbed() -> None:
    # The exact failure mode: the stream was cut mid-JSON.
    leaked = '{"tool_calls": [{"id": "1", "name": "image.generate", "args": {"prompt": "x"'
    out = _scrub_leaked_tool_call_json(leaked)
    assert "tool_calls" not in out
    assert _CLEAN_MARKER in out


def test_json_fenced_envelope_is_scrubbed() -> None:
    leaked = '```json\n{"tool_calls": [{"id": "1", "name": "fs.read", "args": {}}]}\n```'
    out = _scrub_leaked_tool_call_json(leaked)
    assert "tool_calls" not in out
    assert _CLEAN_MARKER in out


def test_plain_fenced_envelope_without_json_tag_is_scrubbed() -> None:
    # Fence with no "json" language tag still unwraps to an envelope.
    leaked = '```\n{"tool_calls": [{"id": "1", "name": "fs.read", "args": {}}]}\n```'
    out = _scrub_leaked_tool_call_json(leaked)
    assert "tool_calls" not in out
    assert _CLEAN_MARKER in out


def test_prose_mentioning_tool_calls_is_untouched() -> None:
    # Conservative: only scrub when the content *is* the envelope.
    prose = "I considered emitting a tool_calls envelope but decided to answer directly instead."
    assert _scrub_leaked_tool_call_json(prose) == prose


def test_normal_prose_untouched() -> None:
    prose = "Here is the summary you asked for."
    assert _scrub_leaked_tool_call_json(prose) == prose


def test_empty_untouched() -> None:
    assert _scrub_leaked_tool_call_json("") == ""
