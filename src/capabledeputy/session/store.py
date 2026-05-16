"""SQLite persistence for the session graph.

The store is async-friendly via anyio.to_thread; SQLite itself is sync
and fast enough that the threadpool overhead is negligible at the rates
expected for a personal assistant. WAL mode is enabled so reads don't
block writes.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

from anyio.to_thread import run_sync as run_in_thread

from capabledeputy.session.model import Session

SCHEMA_VERSION = 4

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    parent_id TEXT,
    status TEXT NOT NULL,
    intent TEXT,
    label_set TEXT NOT NULL,
    capability_set TEXT NOT NULL,
    history TEXT NOT NULL,
    declassification_log TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    owner TEXT,
    tool_aliasing INTEGER NOT NULL DEFAULT 0,
    prefer_programmatic INTEGER NOT NULL DEFAULT 0,
    used_kinds TEXT NOT NULL DEFAULT '[]',
    cap_uses TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (parent_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_sessions_parent ON sessions(parent_id);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at);
"""


class SchemaVersionError(RuntimeError):
    pass


class SessionStore:
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
            conn.executescript(_SCHEMA_SQL)
            row = conn.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (SCHEMA_VERSION,),
                )
                return
            current = row["version"]
            if current == SCHEMA_VERSION:
                return
            if current in (1, 2, 3):
                if current == 1:
                    # v1 → v2: add tool_aliasing and prefer_programmatic columns.
                    col_def = "INTEGER NOT NULL DEFAULT 0"
                    for col in ("tool_aliasing", "prefer_programmatic"):
                        ddl = f"ALTER TABLE sessions ADD COLUMN {col} {col_def}"
                        try:
                            conn.execute(ddl)
                        except sqlite3.OperationalError as e:
                            if "duplicate column" not in str(e).lower():
                                raise
                # v2 → v3: add used_kinds column (JSON array of CapabilityKind
                # values) so tool-identity revocation survives daemon restarts.
                # v2 → v3: used_kinds. v3 → v4: cap_uses. Each ALTER is
                # idempotent (duplicate-column is caught) so a db at any
                # of v1/v2/v3 converges to v4 in one pass.
                for col, default in (
                    ("used_kinds", "'[]'"),
                    ("cap_uses", "'{}'"),
                ):
                    ddl = f"ALTER TABLE sessions ADD COLUMN {col} TEXT NOT NULL DEFAULT {default}"
                    try:
                        conn.execute(ddl)
                    except sqlite3.OperationalError as e:
                        if "duplicate column" not in str(e).lower():
                            raise
                conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
                return
            raise SchemaVersionError(
                f"unsupported schema version {current}; expected {SCHEMA_VERSION}",
            )

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

    async def upsert(self, session: Session) -> None:
        await self.initialize()
        await run_in_thread(self._upsert_sync, session)

    def _upsert_sync(self, session: Session) -> None:
        d = session.to_dict()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    id, parent_id, status, intent,
                    label_set, capability_set, history, declassification_log,
                    created_at, updated_at, owner,
                    tool_aliasing, prefer_programmatic, used_kinds, cap_uses
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    intent = excluded.intent,
                    label_set = excluded.label_set,
                    capability_set = excluded.capability_set,
                    history = excluded.history,
                    declassification_log = excluded.declassification_log,
                    updated_at = excluded.updated_at,
                    owner = excluded.owner,
                    tool_aliasing = excluded.tool_aliasing,
                    prefer_programmatic = excluded.prefer_programmatic,
                    used_kinds = excluded.used_kinds,
                    cap_uses = excluded.cap_uses
                """,
                (
                    d["id"],
                    d["parent"],
                    d["status"],
                    d["intent"],
                    json.dumps(d["label_set"]),
                    json.dumps(d["capability_set"]),
                    json.dumps(d["history"]),
                    json.dumps(d["declassification_log"]),
                    d["created_at"],
                    d["updated_at"],
                    d["owner"],
                    1 if d["tool_aliasing"] else 0,
                    1 if d["prefer_programmatic"] else 0,
                    json.dumps(d["used_kinds"]),
                    json.dumps(d["cap_uses"]),
                ),
            )

    async def get(self, session_id: UUID) -> Session | None:
        await self.initialize()
        return await run_in_thread(self._get_sync, session_id)

    def _get_sync(self, session_id: UUID) -> Session | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?",
                (str(session_id),),
            ).fetchone()
            return _row_to_session(row) if row is not None else None

    async def all(self) -> list[Session]:
        await self.initialize()
        return await run_in_thread(self._all_sync)

    def _all_sync(self) -> list[Session]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY created_at",
            ).fetchall()
            return [_row_to_session(row) for row in rows]


def _row_to_session(row: sqlite3.Row) -> Session:
    return Session.from_dict(
        {
            "id": row["id"],
            "parent": row["parent_id"],
            "status": row["status"],
            "intent": row["intent"],
            "label_set": json.loads(row["label_set"]),
            "capability_set": json.loads(row["capability_set"]),
            "history": json.loads(row["history"]),
            "declassification_log": json.loads(row["declassification_log"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "owner": row["owner"],
            "tool_aliasing": bool(row["tool_aliasing"]),
            "prefer_programmatic": bool(row["prefer_programmatic"]),
            "used_kinds": json.loads(row["used_kinds"]),
            "cap_uses": json.loads(row["cap_uses"]),
        },
    )
