"""Persistent MCP admission decisions.

The adapter can classify tools, but admission is an operator lifecycle:
preview, test, approve, disable, and audit. This store persists that
operator state in the daemon state database so clients only render and relay
decisions.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from anyio.to_thread import run_sync as run_in_thread

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mcp_admission_tools (
    server TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    status TEXT NOT NULL,
    admitted INTEGER NOT NULL,
    capability_kind TEXT NOT NULL,
    target_source TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    decision TEXT NOT NULL,
    warnings TEXT NOT NULL,
    reasons TEXT NOT NULL,
    approved_by TEXT NULL,
    disabled_by TEXT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (server, tool_name)
);

CREATE TABLE IF NOT EXISTS mcp_admission_events (
    event_id TEXT PRIMARY KEY,
    server TEXT NOT NULL,
    tool_name TEXT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mcp_admission_events_server_created
ON mcp_admission_events(server, created_at);
"""


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _loads(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    return json.loads(value)


def _fingerprint(decision: dict[str, Any]) -> str:
    material = {
        "name": decision.get("name", ""),
        "admitted": bool(decision.get("admitted", False)),
        "capability_kind": decision.get("capability_kind", ""),
        "target_source": decision.get("target_source", ""),
        "warnings": decision.get("warnings") or [],
        "reasons": decision.get("reasons") or [],
    }
    return hashlib.sha256(_json(material).encode("utf-8")).hexdigest()


class McpAdmissionStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._initialized = False

    @property
    def path(self) -> Path:
        return self._path

    async def initialize(self) -> None:
        if self._initialized:
            return
        await run_in_thread(self._initialize_sync)
        self._initialized = True

    def _initialize_sync(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self._path), isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
        finally:
            conn.close()

    async def record_preview(
        self, summary: dict[str, Any], *, actor: str = "daemon"
    ) -> dict[str, Any]:
        await self.initialize()
        return await run_in_thread(self._record_preview_sync, summary, actor)

    def _record_preview_sync(self, summary: dict[str, Any], actor: str) -> dict[str, Any]:
        server = str(summary.get("server") or "")
        if not server:
            raise ValueError("admission preview missing server")
        now = _utcnow()
        decisions = []
        with self._connect() as conn:
            for decision in summary.get("decisions") or []:
                if not isinstance(decision, dict):
                    continue
                tool_name = str(decision.get("name") or "")
                if not tool_name:
                    continue
                fp = _fingerprint(decision)
                existing = conn.execute(
                    "SELECT * FROM mcp_admission_tools WHERE server = ? AND tool_name = ?",
                    (server, tool_name),
                ).fetchone()
                if not bool(decision.get("admitted", False)):
                    status = "refused"
                elif existing is not None and existing["status"] == "approved":
                    status = "approved" if existing["fingerprint"] == fp else "needs_reapproval"
                elif existing is not None and existing["status"] == "disabled":
                    status = "disabled"
                else:
                    status = "previewed"
                created_at = existing["created_at"] if existing is not None else now
                approved_by = (
                    existing["approved_by"]
                    if existing is not None and status == "approved"
                    else None
                )
                disabled_by = (
                    existing["disabled_by"]
                    if existing is not None and status == "disabled"
                    else None
                )
                conn.execute(
                    """
                    INSERT INTO mcp_admission_tools (
                        server, tool_name, status, admitted, capability_kind,
                        target_source, fingerprint, decision, warnings, reasons,
                        approved_by, disabled_by, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(server, tool_name) DO UPDATE SET
                        status = excluded.status,
                        admitted = excluded.admitted,
                        capability_kind = excluded.capability_kind,
                        target_source = excluded.target_source,
                        fingerprint = excluded.fingerprint,
                        decision = excluded.decision,
                        warnings = excluded.warnings,
                        reasons = excluded.reasons,
                        approved_by = excluded.approved_by,
                        disabled_by = excluded.disabled_by,
                        updated_at = excluded.updated_at
                    """,
                    (
                        server,
                        tool_name,
                        status,
                        1 if decision.get("admitted") else 0,
                        str(decision.get("capability_kind") or ""),
                        str(decision.get("target_source") or ""),
                        fp,
                        _json(decision),
                        _json(decision.get("warnings") or []),
                        _json(decision.get("reasons") or []),
                        approved_by,
                        disabled_by,
                        created_at,
                        now,
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM mcp_admission_tools WHERE server = ? AND tool_name = ?",
                    (server, tool_name),
                ).fetchone()
                decisions.append(_row_to_tool(row))
            self._event_sync(
                conn,
                server=server,
                tool_name=None,
                action="preview",
                actor=actor,
                payload={"tool_count": len(decisions), "strict": summary.get("strict", True)},
            )
        return {**summary, "decisions": decisions}

    async def approve(
        self,
        *,
        server: str,
        tool_names: list[str],
        actor: str,
    ) -> dict[str, Any]:
        await self.initialize()
        return await run_in_thread(self._set_status_sync, server, tool_names, "approved", actor)

    async def disable(
        self,
        *,
        server: str,
        tool_names: list[str],
        actor: str,
    ) -> dict[str, Any]:
        await self.initialize()
        return await run_in_thread(self._set_status_sync, server, tool_names, "disabled", actor)

    def _set_status_sync(
        self,
        server: str,
        tool_names: list[str],
        status: str,
        actor: str,
    ) -> dict[str, Any]:
        now = _utcnow()
        changed: list[dict[str, Any]] = []
        with self._connect() as conn:
            for tool_name in tool_names:
                row = conn.execute(
                    "SELECT * FROM mcp_admission_tools WHERE server = ? AND tool_name = ?",
                    (server, tool_name),
                ).fetchone()
                if row is None:
                    raise ValueError(f"unknown admission tool {server}.{tool_name}")
                current = _row_to_tool(row)
                if status == "approved" and current["status"] not in {"previewed", "approved"}:
                    raise ValueError(
                        f"{server}.{tool_name} is {current['status']} and cannot be approved",
                    )
                approved_by = actor if status == "approved" else None
                disabled_by = actor if status == "disabled" else None
                conn.execute(
                    """
                    UPDATE mcp_admission_tools
                    SET status = ?, approved_by = ?, disabled_by = ?, updated_at = ?
                    WHERE server = ? AND tool_name = ?
                    """,
                    (status, approved_by, disabled_by, now, server, tool_name),
                )
                updated = conn.execute(
                    "SELECT * FROM mcp_admission_tools WHERE server = ? AND tool_name = ?",
                    (server, tool_name),
                ).fetchone()
                changed.append(_row_to_tool(updated))
                self._event_sync(
                    conn,
                    server=server,
                    tool_name=tool_name,
                    action=status,
                    actor=actor,
                    payload=changed[-1],
                )
        return {"server": server, "tools": changed}

    async def list(self, *, server: str | None = None) -> dict[str, Any]:
        await self.initialize()
        return await run_in_thread(self._list_sync, server)

    def _list_sync(self, server: str | None) -> dict[str, Any]:
        with self._connect() as conn:
            if server:
                rows = conn.execute(
                    "SELECT * FROM mcp_admission_tools WHERE server = ? ORDER BY tool_name",
                    (server,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM mcp_admission_tools ORDER BY server, tool_name",
                ).fetchall()
        return {"tools": [_row_to_tool(row) for row in rows]}

    async def audit(self, *, server: str | None = None, limit: int = 100) -> dict[str, Any]:
        await self.initialize()
        return await run_in_thread(self._audit_sync, server, limit)

    def _audit_sync(self, server: str | None, limit: int) -> dict[str, Any]:
        with self._connect() as conn:
            if server:
                rows = conn.execute(
                    """
                    SELECT * FROM mcp_admission_events
                    WHERE server = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (server, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM mcp_admission_events
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        return {"events": [_row_to_event(row) for row in rows]}

    def _event_sync(
        self,
        conn: sqlite3.Connection,
        *,
        server: str,
        tool_name: str | None,
        action: str,
        actor: str,
        payload: dict[str, Any],
    ) -> None:
        conn.execute(
            """
            INSERT INTO mcp_admission_events (
                event_id, server, tool_name, action, actor, payload, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (str(uuid4()), server, tool_name, action, actor, _json(payload), _utcnow()),
        )


def _row_to_tool(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "server": row["server"],
        "name": row["tool_name"],
        "status": row["status"],
        "admitted": bool(row["admitted"]),
        "capability_kind": row["capability_kind"],
        "target_source": row["target_source"],
        "fingerprint": row["fingerprint"],
        "decision": _loads(row["decision"], {}),
        "warnings": _loads(row["warnings"], []),
        "reasons": _loads(row["reasons"], []),
        "approved_by": row["approved_by"],
        "disabled_by": row["disabled_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "event_id": row["event_id"],
        "server": row["server"],
        "tool_name": row["tool_name"],
        "action": row["action"],
        "actor": row["actor"],
        "payload": _loads(row["payload"], {}),
        "created_at": row["created_at"],
    }
