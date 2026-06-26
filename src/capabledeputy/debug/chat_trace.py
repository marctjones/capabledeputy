"""JSONL chat trace for foreground clients and MLX stream debugging.

Writes one record per line to ``chat-trace.jsonl`` under the CapDep data
dir (override with ``CAPDEP_CHAT_TRACE``). Enabled by default; set
``CAPDEP_CHAT_DEBUG=0`` to disable.

Records turn starts, per-token events, MLX cumulative snapshots (to catch
delta bugs), and finalized LLM output so trash responses are replayable
without the Swift client's local state.
"""

from __future__ import annotations

import json
import os
import threading
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from capabledeputy.paths import default_data_dir

_trace_ctx: ContextVar[dict[str, Any] | None] = ContextVar("chat_trace_ctx", default=None)
_lock = threading.Lock()
_path_override: Path | None = None


def chat_trace_path() -> Path:
    if _path_override is not None:
        return _path_override
    override = os.environ.get("CAPDEP_CHAT_TRACE")
    if override:
        return Path(override)
    return default_data_dir() / "chat-trace.jsonl"


def chat_trace_enabled() -> bool:
    return os.environ.get("CAPDEP_CHAT_DEBUG", "1") != "0"


def set_trace_path(path: Path | None) -> None:
    global _path_override
    _path_override = path


def bind_turn(*, turn_id: str, session_id: str, client_id: str) -> Token:
    return _trace_ctx.set(
        {
            "turn_id": turn_id,
            "session_id": session_id,
            "client_id": client_id,
        },
    )


def unbind(token: Token) -> None:
    _trace_ctx.reset(token)


def snapshot_context() -> dict[str, Any] | None:
    if not chat_trace_enabled():
        return None
    ctx = _trace_ctx.get()
    return dict(ctx) if ctx is not None else None


def _write(record: dict[str, Any]) -> None:
    if not chat_trace_enabled():
        return
    path = chat_trace_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, separators=(",", ":"), default=str) + "\n"
    with _lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)


def log(kind: str, *, ctx: dict[str, Any] | None = None, **payload: Any) -> None:
    base = ctx or _trace_ctx.get() or {}
    if not base and kind not in {"turn_started"}:
        return
    _write(
        {
            "ts": datetime.now(UTC).isoformat(),
            "kind": kind,
            **base,
            **payload,
        },
    )


def log_turn_started(
    *,
    turn_id: str,
    session_id: str,
    client_id: str,
    message: str,
    purpose_handle: str = "",
    stream: str = "",
) -> None:
    preview = message if len(message) <= 500 else message[:497] + "…"
    log(
        "turn_started",
        ctx={
            "turn_id": turn_id,
            "session_id": session_id,
            "client_id": client_id,
        },
        message_preview=preview,
        message_len=len(message),
        purpose_handle=purpose_handle,
        stream=stream,
    )


def log_mlx_chunk(
    ctx: dict[str, Any],
    *,
    delta: str,
    cumulative: str,
    previous_len: int,
) -> None:
    log(
        "mlx_chunk",
        ctx=ctx,
        delta=delta,
        delta_len=len(delta),
        cumulative_len=len(cumulative),
        previous_len=previous_len,
        cumulative_tail=cumulative[-120:],
    )