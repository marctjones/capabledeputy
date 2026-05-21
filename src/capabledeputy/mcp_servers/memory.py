"""Standalone MCP server: persistent key-value store backed by SQLite.

Tools exposed:
  - memory.create(key, value)   create a new entry; refuses overwrite
  - memory.read(key)            read value by key
  - memory.update(key, value)   update an existing entry
  - memory.delete(key)          delete an entry
  - memory.list(prefix='')      list keys matching a prefix

Storage: ~/.local/share/capabledeputy-mcp/memory.sqlite (or
$CAPDEP_MCP_MEMORY_DB if set). Schema is created on first call;
no migration logic.

Run via:
  capdep mcp-server-memory
  python -m capabledeputy.mcp_servers.memory
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path
from typing import Any

from capabledeputy.mcp_servers._common import ToolDescriptor, serve_tools

SERVER_NAME = "capdep-memory"


def _db_path() -> Path:
    override = os.environ.get("CAPDEP_MCP_MEMORY_DB")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "capabledeputy-mcp" / "memory.sqlite"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entries (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
    )
    return conn


async def _create(args: dict[str, Any]) -> dict[str, Any]:
    from datetime import UTC, datetime

    key = str(args["key"]).strip()
    value = str(args["value"])
    if not key:
        raise ValueError("key must be non-empty")
    now = datetime.now(UTC).isoformat(timespec="seconds")
    conn = _connect()
    try:
        try:
            conn.execute(
                "INSERT INTO entries (key, value, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (key, value, now, now),
            )
        except sqlite3.IntegrityError as e:
            raise ValueError(f"entry exists; use memory.update to overwrite: {key}") from e
        return {"key": key, "size": len(value), "created_at": now}
    finally:
        conn.close()


async def _read(args: dict[str, Any]) -> dict[str, Any]:
    key = str(args["key"])
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT key, value, created_at, updated_at FROM entries WHERE key = ?",
            (key,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ValueError(f"no such key: {key}")
    return {
        "key": row[0],
        "value": row[1],
        "created_at": row[2],
        "updated_at": row[3],
    }


async def _update(args: dict[str, Any]) -> dict[str, Any]:
    from datetime import UTC, datetime

    key = str(args["key"])
    value = str(args["value"])
    now = datetime.now(UTC).isoformat(timespec="seconds")
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE entries SET value = ?, updated_at = ? WHERE key = ?",
            (value, now, key),
        )
        if cur.rowcount == 0:
            raise ValueError(f"no such key; use memory.create: {key}")
        return {"key": key, "size": len(value), "updated_at": now}
    finally:
        conn.close()


async def _delete(args: dict[str, Any]) -> dict[str, Any]:
    key = str(args["key"])
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM entries WHERE key = ?", (key,))
        if cur.rowcount == 0:
            raise ValueError(f"no such key: {key}")
        return {"key": key, "deleted": True}
    finally:
        conn.close()


async def _list(args: dict[str, Any]) -> dict[str, Any]:
    prefix = str(args.get("prefix", ""))
    conn = _connect()
    try:
        if prefix:
            rows = conn.execute(
                "SELECT key, updated_at FROM entries WHERE key LIKE ? ORDER BY key",
                (prefix + "%",),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT key, updated_at FROM entries ORDER BY key",
            ).fetchall()
    finally:
        conn.close()
    return {
        "count": len(rows),
        "entries": [{"key": r[0], "updated_at": r[1]} for r in rows],
    }


def tools() -> list[ToolDescriptor]:
    return [
        ToolDescriptor(
            name="memory.create",
            description="Create a new memory entry. Refuses if the key already exists.",
            input_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["key", "value"],
            },
            handler=_create,
            annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
        ),
        ToolDescriptor(
            name="memory.read",
            description="Read a memory entry by key.",
            input_schema={
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
            },
            handler=_read,
            annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
        ),
        ToolDescriptor(
            name="memory.update",
            description="Update an existing memory entry. Refuses if the key does not yet exist.",
            input_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["key", "value"],
            },
            handler=_update,
            annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False},
        ),
        ToolDescriptor(
            name="memory.delete",
            description="Delete a memory entry by key.",
            input_schema={
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
            },
            handler=_delete,
            annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False},
        ),
        ToolDescriptor(
            name="memory.list",
            description="List keys; optionally filter by prefix.",
            input_schema={
                "type": "object",
                "properties": {"prefix": {"type": "string"}},
            },
            handler=_list,
            annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
        ),
    ]


async def serve() -> None:
    await serve_tools(SERVER_NAME, tools())


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
