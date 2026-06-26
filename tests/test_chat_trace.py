from __future__ import annotations

import json
from pathlib import Path

from capabledeputy.debug.chat_trace import (
    bind_turn,
    chat_trace_enabled,
    log,
    log_turn_started,
    set_trace_path,
    unbind,
)


def test_chat_trace_writes_turn_and_token_records(tmp_path: Path) -> None:
    trace_file = tmp_path / "chat-trace.jsonl"
    set_trace_path(trace_file)
    log_turn_started(
        turn_id="turn-1",
        session_id="sess-1",
        client_id="CapDepMac",
        message="hello there",
        purpose_handle="general",
        stream="turn:turn-1",
    )
    token = bind_turn(turn_id="turn-1", session_id="sess-1", client_id="CapDepMac")
    try:
        log("llm_token", text="hel", partial_content="hel")
    finally:
        unbind(token)

    lines = trace_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    started = json.loads(lines[0])
    token_event = json.loads(lines[1])
    assert started["kind"] == "turn_started"
    assert started["client_id"] == "CapDepMac"
    assert started["message_preview"] == "hello there"
    assert token_event["kind"] == "llm_token"
    assert token_event["text"] == "hel"


def test_chat_trace_enabled_by_default() -> None:
    assert chat_trace_enabled() is True