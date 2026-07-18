"""#323 — dependency-free structured (JSON) event log for the daemon.

The codebase had no stdlib `logging`; daemon output was human-only Rich-to-stderr
gated on `--verbose`. This adds a tiny always-available structured event log so
degraded health (turn errors, tool denials, upstream-down, DB recovery) is
observable as machine-parseable JSON lines — WITHOUT pulling structlog or the
opentelemetry SDK (either would be a trust-boundary dependency).

Sink + format are env-driven (resolved once, cached; `reset_logger()` re-reads):
  CAPDEP_LOG_FORMAT = json (default) | text | off
  CAPDEP_LOG_LEVEL  = debug | info | warning | error         (default: info)
  CAPDEP_LOG_FILE   = <path>                                  (default: stderr)

One entry point: `log_event(level, event, **fields)`. Levels below the threshold
are dropped. JSON keys are stable (`ts`, `level`, `event`, then fields) so a log
processor / `capdep doctor` can filter without guessing. This is the house
pattern — call `log_event(...)`, do not reach for stdlib logging.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import UTC, datetime
from typing import Any, TextIO

# Ordered severity. A record is emitted iff its level >= the configured floor.
_LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40}


class StructuredLogger:
    """Writes one JSON (or text) line per event to a stream. Thread-safe."""

    def __init__(self, *, stream: TextIO, fmt: str = "json", level: str = "info") -> None:
        self._stream = stream
        self._fmt = fmt
        self._floor = _LEVELS.get(level, 20)
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._fmt != "off"

    def log(self, level: str, event: str, **fields: Any) -> None:
        if self._fmt == "off":
            return
        if _LEVELS.get(level, 20) < self._floor:
            return
        record: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": level,
            "event": event,
            **fields,
        }
        line = _render(record) if self._fmt == "text" else _render_json(record)
        with self._lock:
            self._stream.write(line + "\n")
            self._stream.flush()


def _render_json(record: dict[str, Any]) -> str:
    # default=str so a UUID / Path / datetime field never crashes the log path.
    return json.dumps(record, separators=(",", ":"), default=str)


def _render(record: dict[str, Any]) -> str:
    ts = record.get("ts", "")
    level = str(record.get("level", "")).upper()
    event = record.get("event", "")
    extras = " ".join(f"{k}={v}" for k, v in record.items() if k not in ("ts", "level", "event"))
    return f"{ts} [{level}] {event}" + (f" {extras}" if extras else "")


_default: StructuredLogger | None = None
_default_lock = threading.Lock()


def _build_from_env() -> StructuredLogger:
    fmt = (os.environ.get("CAPDEP_LOG_FORMAT") or "json").strip().lower()
    if fmt not in ("json", "text", "off"):
        fmt = "json"
    level = (os.environ.get("CAPDEP_LOG_LEVEL") or "info").strip().lower()
    if level not in _LEVELS:
        level = "info"
    path = os.environ.get("CAPDEP_LOG_FILE")
    stream: TextIO = sys.stderr
    if path:
        try:
            # Append, line-buffered; a bad path must never take the daemon down,
            # so fall back to stderr on any open error.
            stream = open(path, "a", buffering=1, encoding="utf-8")  # noqa: SIM115
        except OSError:
            stream = sys.stderr
    return StructuredLogger(stream=stream, fmt=fmt, level=level)


def get_logger() -> StructuredLogger:
    """Process-wide structured logger, built lazily from the environment."""
    global _default
    if _default is None:
        with _default_lock:
            if _default is None:
                _default = _build_from_env()
    return _default


def set_logger(logger: StructuredLogger | None) -> None:
    """Override the process logger (tests / an explicit daemon sink)."""
    global _default
    with _default_lock:
        _default = logger


def reset_logger() -> None:
    """Drop the cached logger so the next `get_logger()` re-reads the env."""
    set_logger(None)


def log_event(level: str, event: str, **fields: Any) -> None:
    """Emit one structured event. This is the house logging call."""
    get_logger().log(level, event, **fields)
