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

SCHEMA_VERSION = 7

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
    revoked_audit_ids TEXT NOT NULL DEFAULT '[]',
    -- 003 v0.9 four-axis storage shape (T008, FR-045). axis_c lives on
    -- capability_set/ToolDefinition, not on the session.
    axis_a TEXT NOT NULL DEFAULT '[]',
    axis_b TEXT NOT NULL DEFAULT '[]',
    axis_d TEXT NOT NULL DEFAULT '{}',
    purpose_handle TEXT NOT NULL DEFAULT 'unset',
    reference_handles TEXT NOT NULL DEFAULT '{}',
    risk_preference_at_spawn TEXT NOT NULL DEFAULT 'cautious',
    effective_isolation_region_id TEXT NULL,
    clearance_profile_id TEXT NULL,
    -- Cookbook Pattern ⑥ — STRICT (default) | SHADOW.
    enforcement_mode TEXT NOT NULL DEFAULT 'strict',
    -- Cookbook §4 #6 — first-action-of-kind prompt.
    first_use_prompt_enabled INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (parent_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_sessions_parent ON sessions(parent_id);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at);

-- 003 v0.9 operator-config-derived tables (T009). Source-of-truth is
-- the corresponding configs/*.yaml or .json file; these tables hold
-- the loaded form for fast lookup + provenance audit.

CREATE TABLE IF NOT EXISTS source_location_bindings (
    name TEXT PRIMARY KEY,
    scope_pattern_canonical TEXT NOT NULL,
    category TEXT NOT NULL,
    default_tier TEXT NOT NULL,
    reversibility TEXT NULL,
    mutability TEXT NULL,
    write_discipline TEXT NULL,
    risk_ids TEXT NOT NULL,
    assignment_provenance TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS relationship_groups (
    group_id TEXT PRIMARY KEY,
    member_principal_ids TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS expectation_bindings (
    binding_id TEXT PRIMARY KEY,
    initiator TEXT NOT NULL,
    effect_kind TEXT NOT NULL,
    time_window TEXT NULL,
    param_constraints TEXT NULL,
    risk_ids TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS purposes (
    purpose_id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    admissible_categories TEXT NULL,
    inadmissible_categories TEXT NULL,
    recommended_pattern TEXT NULL
);

CREATE TABLE IF NOT EXISTS override_policies (
    tier_or_floor TEXT PRIMARY KEY,
    policy TEXT NOT NULL,
    authorized_principal_ids TEXT NOT NULL,
    attester_principal_ids TEXT NULL,
    expiry_seconds INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS override_grants (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    action_kind TEXT NOT NULL,
    target TEXT NOT NULL,
    target_category_tier TEXT NOT NULL,
    hard_floor_crossed TEXT NOT NULL,
    invoker_principal TEXT NOT NULL,
    attester_principal TEXT NULL,
    override_policy_at_grant TEXT NOT NULL,
    friction_level TEXT NOT NULL,
    audit_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT NULL,
    state TEXT NOT NULL DEFAULT 'active',
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_override_grants_session ON override_grants(session_id);
CREATE INDEX IF NOT EXISTS idx_override_grants_expires ON override_grants(expires_at);
"""


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
        # No backwards compatibility (label-model redesign, §R6). The
        # four-axis cutover (schema v7) removes every legacy read path;
        # a db at any other schema version is WIPED, not migrated —
        # single-operator, local, no migration. Old sessions carried the
        # fused flat `label_set`; there is no faithful four-axis
        # reconstruction worth preserving, so we start clean.
        if self._needs_wipe():
            self._path.unlink(missing_ok=True)
            for suffix in ("-wal", "-shm"):
                self._path.with_name(self._path.name + suffix).unlink(missing_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)
            row = conn.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (SCHEMA_VERSION,),
                )

    def _needs_wipe(self) -> bool:
        """True iff an existing db is at any schema version other than
        the current one (or is unreadable). A fresh/absent db never needs
        a wipe — it is created clean from `_SCHEMA_SQL`."""
        if not self._path.exists():
            return False
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT version FROM schema_version").fetchone()
        except sqlite3.DatabaseError:
            return True  # corrupt / not-a-db / missing schema_version table
        if row is None:
            return True
        return bool(row["version"] != SCHEMA_VERSION)

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
                    tool_aliasing, prefer_programmatic, used_kinds, cap_uses,
                    revoked_audit_ids,
                    axis_a, axis_b, axis_d, purpose_handle,
                    reference_handles, risk_preference_at_spawn,
                    effective_isolation_region_id, enforcement_mode,
                    first_use_prompt_enabled
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    cap_uses = excluded.cap_uses,
                    revoked_audit_ids = excluded.revoked_audit_ids,
                    axis_a = excluded.axis_a,
                    axis_b = excluded.axis_b,
                    axis_d = excluded.axis_d,
                    purpose_handle = excluded.purpose_handle,
                    reference_handles = excluded.reference_handles,
                    risk_preference_at_spawn = excluded.risk_preference_at_spawn,
                    effective_isolation_region_id = excluded.effective_isolation_region_id,
                    enforcement_mode = excluded.enforcement_mode,
                    first_use_prompt_enabled = excluded.first_use_prompt_enabled
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
                    json.dumps(d["revoked_audit_ids"]),
                    json.dumps(d["axis_a"]),
                    json.dumps(d["axis_b"]),
                    json.dumps(d["axis_d"]),
                    d["purpose_handle"],
                    json.dumps(d["reference_handles"]),
                    d["risk_preference_at_spawn"],
                    d["effective_isolation_region_id"],
                    d.get("enforcement_mode", "strict"),
                    1 if d.get("first_use_prompt_enabled", False) else 0,
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
    # 003 T010 — default-tolerant reads for the v0.9 columns. A row
    # written at SCHEMA_VERSION 5 (pre-migration in-memory copy) will
    # not have these columns; sqlite3.Row raises IndexError on a
    # missing column, so we probe with `try` and fall back to defaults.
    def _col(name: str, default: object) -> object:
        try:
            value = row[name]
        except IndexError:
            return default
        return value if value is not None else default

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
            "revoked_audit_ids": json.loads(row["revoked_audit_ids"]),
            "axis_a": json.loads(str(_col("axis_a", "[]"))),
            "axis_b": json.loads(str(_col("axis_b", "[]"))),
            "axis_d": json.loads(str(_col("axis_d", "{}"))),
            "purpose_handle": _col("purpose_handle", "unset"),
            "reference_handles": json.loads(str(_col("reference_handles", "{}"))),
            "risk_preference_at_spawn": _col("risk_preference_at_spawn", "cautious"),
            "effective_isolation_region_id": _col("effective_isolation_region_id", None),
            "enforcement_mode": _col("enforcement_mode", "strict"),
            "first_use_prompt_enabled": bool(_col("first_use_prompt_enabled", 0)),
        },
    )


