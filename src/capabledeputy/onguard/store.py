"""Persistent daemon coordination store for onguard clients.

Onguard clients are normal headless clients. This store provides shared daemon
state for their identity, approved configuration, queued commands, events, and
schedules. It is deliberately separate from `memory.*`: memory is labeled user
data, while this is control-plane coordination state with labels/provenance on
each record.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from anyio.to_thread import run_sync as run_in_thread


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def _loads(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    return json.loads(value)


def _parse_iso(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _next_run_at(recurrence: dict[str, Any], *, after: datetime | None = None) -> str | None:
    """Compute the next deterministic run time for simple onguard schedules."""
    if not recurrence:
        return None
    base = after or _utcnow()
    kind = str(recurrence.get("kind", "manual"))
    if kind == "manual":
        return None
    if kind == "interval":
        seconds = int(recurrence.get("seconds", 0))
        if seconds <= 0:
            raise ValueError("interval recurrence requires positive seconds")
        return _iso(base + timedelta(seconds=seconds))
    if kind == "daily":
        hour = int(recurrence.get("hour", 0))
        minute = int(recurrence.get("minute", 0))
        candidate = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= base:
            candidate += timedelta(days=1)
        return _iso(candidate)
    raise ValueError(f"unsupported onguard recurrence kind: {kind}")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS onguard_clients (
    client_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    owner TEXT NULL,
    version TEXT NULL,
    allowed_schedules TEXT NOT NULL,
    metadata TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS onguard_configs (
    config_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    schema_name TEXT NOT NULL,
    payload TEXT NOT NULL,
    labels TEXT NOT NULL,
    status TEXT NOT NULL,
    proposed_by TEXT NOT NULL,
    approved_by TEXT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (client_id) REFERENCES onguard_clients(client_id)
);

CREATE TABLE IF NOT EXISTS onguard_commands (
    command_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    command TEXT NOT NULL,
    payload TEXT NOT NULL,
    labels TEXT NOT NULL,
    provenance TEXT NOT NULL,
    status TEXT NOT NULL,
    created_by TEXT NOT NULL,
    claimed_by TEXT NULL,
    lease_until TEXT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    result TEXT NULL,
    artifact_ref TEXT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (client_id) REFERENCES onguard_clients(client_id)
);

CREATE INDEX IF NOT EXISTS idx_onguard_commands_client_status
ON onguard_commands(client_id, status, created_at);

CREATE TABLE IF NOT EXISTS onguard_events (
    event_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    command_id TEXT NULL,
    schedule_id TEXT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    labels TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (client_id) REFERENCES onguard_clients(client_id)
);

CREATE INDEX IF NOT EXISTS idx_onguard_events_client_created
ON onguard_events(client_id, created_at);

CREATE TABLE IF NOT EXISTS onguard_event_acks (
    event_id TEXT PRIMARY KEY,
    acknowledged_by TEXT NOT NULL,
    acknowledged_at TEXT NOT NULL,
    FOREIGN KEY (event_id) REFERENCES onguard_events(event_id)
);

CREATE TABLE IF NOT EXISTS onguard_schedules (
    schedule_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    recurrence TEXT NOT NULL,
    command TEXT NOT NULL,
    payload TEXT NOT NULL,
    labels TEXT NOT NULL,
    status TEXT NOT NULL,
    created_by TEXT NOT NULL,
    approved_by TEXT NULL,
    last_run_at TEXT NULL,
    next_run_at TEXT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (client_id) REFERENCES onguard_clients(client_id)
);

CREATE INDEX IF NOT EXISTS idx_onguard_schedules_client_status
ON onguard_schedules(client_id, status);

CREATE TABLE IF NOT EXISTS onguard_artifacts (
    artifact_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    command_id TEXT NULL,
    schedule_id TEXT NULL,
    session_id TEXT NULL,
    artifact_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    labels TEXT NOT NULL,
    provenance TEXT NOT NULL,
    status TEXT NOT NULL,
    created_by TEXT NOT NULL,
    promoted_by TEXT NULL,
    deleted_at TEXT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (client_id) REFERENCES onguard_clients(client_id)
);

CREATE INDEX IF NOT EXISTS idx_onguard_artifacts_client_status
ON onguard_artifacts(client_id, status, created_at);

CREATE TABLE IF NOT EXISTS onguard_schedule_runs (
    run_id TEXT PRIMARY KEY,
    schedule_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    command_id TEXT NULL,
    status TEXT NOT NULL,
    claimed_by TEXT NULL,
    lease_until TEXT NULL,
    run_after TEXT NOT NULL,
    started_at TEXT NULL,
    finished_at TEXT NULL,
    result TEXT NULL,
    artifact_ref TEXT NULL,
    error TEXT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (schedule_id) REFERENCES onguard_schedules(schedule_id),
    FOREIGN KEY (client_id) REFERENCES onguard_clients(client_id)
);

CREATE INDEX IF NOT EXISTS idx_onguard_schedule_runs_schedule_status
ON onguard_schedule_runs(schedule_id, status, run_after);
"""


class OnguardStore:
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

    async def register_client(
        self,
        *,
        client_id: str,
        kind: str,
        owner: str | None = None,
        version: str | None = None,
        allowed_schedules: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        status: str = "active",
    ) -> dict[str, Any]:
        await self.initialize()
        return await run_in_thread(
            self._register_client_sync,
            client_id,
            kind,
            owner,
            version,
            allowed_schedules or [],
            metadata or {},
            status,
        )

    def _register_client_sync(
        self,
        client_id: str,
        kind: str,
        owner: str | None,
        version: str | None,
        allowed_schedules: list[str],
        metadata: dict[str, Any],
        status: str,
    ) -> dict[str, Any]:
        now = _iso(_utcnow())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO onguard_clients (
                    client_id, kind, owner, version, allowed_schedules,
                    metadata, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(client_id) DO UPDATE SET
                    kind = excluded.kind,
                    owner = excluded.owner,
                    version = excluded.version,
                    allowed_schedules = excluded.allowed_schedules,
                    metadata = excluded.metadata,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (
                    client_id,
                    kind,
                    owner,
                    version,
                    _json(allowed_schedules),
                    _json(metadata),
                    status,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM onguard_clients WHERE client_id = ?",
                (client_id,),
            ).fetchone()
        return _client(row)

    async def list_clients(self, *, kind: str | None = None) -> list[dict[str, Any]]:
        await self.initialize()
        return await run_in_thread(self._list_clients_sync, kind)

    def _list_clients_sync(self, kind: str | None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if kind is None:
                rows = conn.execute(
                    "SELECT * FROM onguard_clients ORDER BY client_id",
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM onguard_clients WHERE kind = ? ORDER BY client_id",
                    (kind,),
                ).fetchall()
        return [_client(row) for row in rows]

    async def propose_config(
        self,
        *,
        config_id: str,
        client_id: str,
        schema_name: str,
        payload: dict[str, Any],
        labels: list[str] | None,
        proposed_by: str,
        status: str = "proposed",
    ) -> dict[str, Any]:
        await self.initialize()
        return await run_in_thread(
            self._propose_config_sync,
            config_id,
            client_id,
            schema_name,
            payload,
            labels or [],
            proposed_by,
            status,
        )

    def _propose_config_sync(
        self,
        config_id: str,
        client_id: str,
        schema_name: str,
        payload: dict[str, Any],
        labels: list[str],
        proposed_by: str,
        status: str,
    ) -> dict[str, Any]:
        now = _iso(_utcnow())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO onguard_configs (
                    config_id, client_id, schema_name, payload, labels,
                    status, proposed_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(config_id) DO UPDATE SET
                    payload = excluded.payload,
                    labels = excluded.labels,
                    status = excluded.status,
                    proposed_by = excluded.proposed_by,
                    approved_by = NULL,
                    updated_at = excluded.updated_at
                """,
                (
                    config_id,
                    client_id,
                    schema_name,
                    _json(payload),
                    _json(labels),
                    status,
                    proposed_by,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM onguard_configs WHERE config_id = ?",
                (config_id,),
            ).fetchone()
        return _config(row)

    async def approve_config(self, *, config_id: str, approved_by: str) -> dict[str, Any]:
        await self.initialize()
        return await run_in_thread(self._approve_config_sync, config_id, approved_by)

    def _approve_config_sync(self, config_id: str, approved_by: str) -> dict[str, Any]:
        now = _iso(_utcnow())
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE onguard_configs
                SET status = 'approved', approved_by = ?, updated_at = ?
                WHERE config_id = ?
                """,
                (approved_by, now, config_id),
            )
            row = conn.execute(
                "SELECT * FROM onguard_configs WHERE config_id = ?",
                (config_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"onguard config not found: {config_id}")
        return _config(row)

    async def list_configs(
        self,
        *,
        client_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        await self.initialize()
        return await run_in_thread(self._list_configs_sync, client_id, status)

    def _list_configs_sync(
        self,
        client_id: str | None,
        status: str | None,
    ) -> list[dict[str, Any]]:
        clauses = []
        args: list[str] = []
        if client_id:
            clauses.append("client_id = ?")
            args.append(client_id)
        if status:
            clauses.append("status = ?")
            args.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM onguard_configs{where} ORDER BY updated_at DESC",
                args,
            ).fetchall()
        return [_config(row) for row in rows]

    async def enqueue_command(
        self,
        *,
        client_id: str,
        command: str,
        payload: dict[str, Any],
        labels: list[str] | None,
        provenance: dict[str, Any] | None,
        created_by: str,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        await self.initialize()
        return await run_in_thread(
            self._enqueue_command_sync,
            command_id or str(uuid4()),
            client_id,
            command,
            payload,
            labels or [],
            provenance or {},
            created_by,
        )

    def _enqueue_command_sync(
        self,
        command_id: str,
        client_id: str,
        command: str,
        payload: dict[str, Any],
        labels: list[str],
        provenance: dict[str, Any],
        created_by: str,
    ) -> dict[str, Any]:
        now = _iso(_utcnow())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO onguard_commands (
                    command_id, client_id, command, payload, labels,
                    provenance, status, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                """,
                (
                    command_id,
                    client_id,
                    command,
                    _json(payload),
                    _json(labels),
                    _json(provenance),
                    created_by,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM onguard_commands WHERE command_id = ?",
                (command_id,),
            ).fetchone()
        return _command(row)

    async def claim_command(
        self,
        *,
        client_id: str,
        claimed_by: str,
        lease_seconds: int = 300,
        command_id: str | None = None,
    ) -> dict[str, Any] | None:
        await self.initialize()
        return await run_in_thread(
            self._claim_command_sync,
            client_id,
            claimed_by,
            lease_seconds,
            command_id,
        )

    def _claim_command_sync(
        self,
        client_id: str,
        claimed_by: str,
        lease_seconds: int,
        command_id: str | None,
    ) -> dict[str, Any] | None:
        now_dt = _utcnow()
        now = _iso(now_dt)
        lease_until = _iso(now_dt + timedelta(seconds=max(1, lease_seconds)))
        with self._connect() as conn:
            if command_id is None:
                row = conn.execute(
                    """
                    SELECT * FROM onguard_commands
                    WHERE client_id = ? AND status = 'queued'
                    ORDER BY created_at
                    LIMIT 1
                    """,
                    (client_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT * FROM onguard_commands
                    WHERE client_id = ? AND command_id = ? AND status = 'queued'
                    """,
                    (client_id, command_id),
                ).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                UPDATE onguard_commands
                SET status = 'claimed', claimed_by = ?, lease_until = ?,
                    attempts = attempts + 1, updated_at = ?
                WHERE command_id = ?
                """,
                (claimed_by, lease_until, now, row["command_id"]),
            )
            updated = conn.execute(
                "SELECT * FROM onguard_commands WHERE command_id = ?",
                (row["command_id"],),
            ).fetchone()
        return _command(updated)

    async def complete_command(
        self,
        *,
        command_id: str,
        result: dict[str, Any],
        artifact_ref: str | None = None,
    ) -> dict[str, Any]:
        return await self._finish_command(
            command_id=command_id,
            status="completed",
            result=result,
            artifact_ref=artifact_ref,
        )

    async def fail_command(
        self,
        *,
        command_id: str,
        result: dict[str, Any],
        artifact_ref: str | None = None,
    ) -> dict[str, Any]:
        return await self._finish_command(
            command_id=command_id,
            status="failed",
            result=result,
            artifact_ref=artifact_ref,
        )

    async def _finish_command(
        self,
        *,
        command_id: str,
        status: str,
        result: dict[str, Any],
        artifact_ref: str | None,
    ) -> dict[str, Any]:
        await self.initialize()
        return await run_in_thread(
            self._finish_command_sync,
            command_id,
            status,
            result,
            artifact_ref,
        )

    def _finish_command_sync(
        self,
        command_id: str,
        status: str,
        result: dict[str, Any],
        artifact_ref: str | None,
    ) -> dict[str, Any]:
        now = _iso(_utcnow())
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE onguard_commands
                SET status = ?, result = ?, artifact_ref = ?, updated_at = ?
                WHERE command_id = ?
                """,
                (status, _json(result), artifact_ref, now, command_id),
            )
            row = conn.execute(
                "SELECT * FROM onguard_commands WHERE command_id = ?",
                (command_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"onguard command not found: {command_id}")
        return _command(row)

    async def list_commands(
        self,
        *,
        client_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        await self.initialize()
        return await run_in_thread(self._list_commands_sync, client_id, status)

    def _list_commands_sync(
        self,
        client_id: str | None,
        status: str | None,
    ) -> list[dict[str, Any]]:
        clauses = []
        args: list[str] = []
        if client_id:
            clauses.append("client_id = ?")
            args.append(client_id)
        if status:
            clauses.append("status = ?")
            args.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM onguard_commands{where} ORDER BY created_at",
                args,
            ).fetchall()
        return [_command(row) for row in rows]

    async def publish_event(
        self,
        *,
        client_id: str,
        event_type: str,
        payload: dict[str, Any],
        labels: list[str] | None,
        command_id: str | None = None,
        schedule_id: str | None = None,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        await self.initialize()
        return await run_in_thread(
            self._publish_event_sync,
            event_id or str(uuid4()),
            client_id,
            command_id,
            schedule_id,
            event_type,
            payload,
            labels or [],
        )

    def _publish_event_sync(
        self,
        event_id: str,
        client_id: str,
        command_id: str | None,
        schedule_id: str | None,
        event_type: str,
        payload: dict[str, Any],
        labels: list[str],
    ) -> dict[str, Any]:
        now = _iso(_utcnow())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO onguard_events (
                    event_id, client_id, command_id, schedule_id,
                    event_type, payload, labels, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    client_id,
                    command_id,
                    schedule_id,
                    event_type,
                    _json(payload),
                    _json(labels),
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM onguard_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        return _event(row)

    async def list_events(
        self,
        *,
        client_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        await self.initialize()
        return await run_in_thread(self._list_events_sync, client_id, limit)

    def _list_events_sync(self, client_id: str | None, limit: int) -> list[dict[str, Any]]:
        bounded = max(1, min(limit, 500))
        with self._connect() as conn:
            if client_id:
                rows = conn.execute(
                    """
                    SELECT e.*, a.acknowledged_by, a.acknowledged_at
                    FROM onguard_events e
                    LEFT JOIN onguard_event_acks a ON a.event_id = e.event_id
                    WHERE e.client_id = ?
                    ORDER BY e.created_at DESC
                    LIMIT ?
                    """,
                    (client_id, bounded),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT e.*, a.acknowledged_by, a.acknowledged_at
                    FROM onguard_events e
                    LEFT JOIN onguard_event_acks a ON a.event_id = e.event_id
                    ORDER BY e.created_at DESC
                    LIMIT ?
                    """,
                    (bounded,),
                ).fetchall()
        return [_event(row) for row in rows]

    async def acknowledge_event(
        self,
        *,
        event_id: str,
        acknowledged_by: str,
    ) -> dict[str, Any]:
        await self.initialize()
        return await run_in_thread(self._acknowledge_event_sync, event_id, acknowledged_by)

    def _acknowledge_event_sync(self, event_id: str, acknowledged_by: str) -> dict[str, Any]:
        now = _iso(_utcnow())
        with self._connect() as conn:
            event = conn.execute(
                "SELECT event_id FROM onguard_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if event is None:
                raise KeyError(f"onguard event not found: {event_id}")
            conn.execute(
                """
                INSERT INTO onguard_event_acks (event_id, acknowledged_by, acknowledged_at)
                VALUES (?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    acknowledged_by = excluded.acknowledged_by,
                    acknowledged_at = excluded.acknowledged_at
                """,
                (event_id, acknowledged_by, now),
            )
            row = conn.execute(
                """
                SELECT e.*, a.acknowledged_by, a.acknowledged_at
                FROM onguard_events e
                LEFT JOIN onguard_event_acks a ON a.event_id = e.event_id
                WHERE e.event_id = ?
                """,
                (event_id,),
            ).fetchone()
        return _event(row)

    async def create_artifact(
        self,
        *,
        artifact_id: str | None = None,
        client_id: str,
        artifact_type: str,
        payload: dict[str, Any],
        labels: list[str] | None,
        provenance: dict[str, Any] | None,
        created_by: str,
        command_id: str | None = None,
        schedule_id: str | None = None,
        session_id: str | None = None,
        status: str = "draft",
    ) -> dict[str, Any]:
        await self.initialize()
        return await run_in_thread(
            self._create_artifact_sync,
            artifact_id or str(uuid4()),
            client_id,
            command_id,
            schedule_id,
            session_id,
            artifact_type,
            payload,
            labels or [],
            provenance or {},
            status,
            created_by,
        )

    def _create_artifact_sync(
        self,
        artifact_id: str,
        client_id: str,
        command_id: str | None,
        schedule_id: str | None,
        session_id: str | None,
        artifact_type: str,
        payload: dict[str, Any],
        labels: list[str],
        provenance: dict[str, Any],
        status: str,
        created_by: str,
    ) -> dict[str, Any]:
        now = _iso(_utcnow())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO onguard_artifacts (
                    artifact_id, client_id, command_id, schedule_id, session_id,
                    artifact_type, payload, labels, provenance, status,
                    created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    client_id,
                    command_id,
                    schedule_id,
                    session_id,
                    artifact_type,
                    _json(payload),
                    _json(labels),
                    _json(provenance),
                    status,
                    created_by,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM onguard_artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        return _artifact(row)

    async def read_artifact(self, *, artifact_id: str) -> dict[str, Any]:
        await self.initialize()
        return await run_in_thread(self._read_artifact_sync, artifact_id)

    def _read_artifact_sync(self, artifact_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM onguard_artifacts WHERE artifact_id = ? AND deleted_at IS NULL",
                (artifact_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"onguard artifact not found: {artifact_id}")
        return _artifact(row)

    async def list_artifacts(
        self,
        *,
        client_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        await self.initialize()
        return await run_in_thread(self._list_artifacts_sync, client_id, status)

    def _list_artifacts_sync(
        self,
        client_id: str | None,
        status: str | None,
    ) -> list[dict[str, Any]]:
        clauses = ["deleted_at IS NULL"]
        args: list[str] = []
        if client_id:
            clauses.append("client_id = ?")
            args.append(client_id)
        if status:
            clauses.append("status = ?")
            args.append(status)
        where = " WHERE " + " AND ".join(clauses)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM onguard_artifacts{where} ORDER BY created_at DESC",
                args,
            ).fetchall()
        return [_artifact(row) for row in rows]

    async def promote_artifact(
        self,
        *,
        artifact_id: str,
        promoted_by: str,
        status: str = "promoted",
    ) -> dict[str, Any]:
        await self.initialize()
        return await run_in_thread(
            self._promote_artifact_sync,
            artifact_id,
            promoted_by,
            status,
        )

    def _promote_artifact_sync(
        self,
        artifact_id: str,
        promoted_by: str,
        status: str,
    ) -> dict[str, Any]:
        now = _iso(_utcnow())
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE onguard_artifacts
                SET status = ?, promoted_by = ?, updated_at = ?
                WHERE artifact_id = ? AND deleted_at IS NULL
                """,
                (status, promoted_by, now, artifact_id),
            )
            row = conn.execute(
                "SELECT * FROM onguard_artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        if row is None or row["deleted_at"] is not None:
            raise KeyError(f"onguard artifact not found: {artifact_id}")
        return _artifact(row)

    async def delete_artifact(self, *, artifact_id: str) -> dict[str, Any]:
        await self.initialize()
        return await run_in_thread(self._delete_artifact_sync, artifact_id)

    def _delete_artifact_sync(self, artifact_id: str) -> dict[str, Any]:
        now = _iso(_utcnow())
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE onguard_artifacts
                SET status = 'deleted', deleted_at = ?, updated_at = ?
                WHERE artifact_id = ? AND deleted_at IS NULL
                """,
                (now, now, artifact_id),
            )
            row = conn.execute(
                "SELECT * FROM onguard_artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"onguard artifact not found: {artifact_id}")
        return _artifact(row)

    async def create_schedule(
        self,
        *,
        schedule_id: str,
        client_id: str,
        recurrence: dict[str, Any],
        command: str,
        payload: dict[str, Any],
        labels: list[str] | None,
        created_by: str,
        approved_by: str | None = None,
        next_run_at: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        await self.initialize()
        return await run_in_thread(
            self._create_schedule_sync,
            schedule_id,
            client_id,
            recurrence,
            command,
            payload,
            labels or [],
            created_by,
            approved_by,
            next_run_at,
            status or ("active" if approved_by else "proposed"),
        )

    def _create_schedule_sync(
        self,
        schedule_id: str,
        client_id: str,
        recurrence: dict[str, Any],
        command: str,
        payload: dict[str, Any],
        labels: list[str],
        created_by: str,
        approved_by: str | None,
        next_run_at: str | None,
        status: str,
    ) -> dict[str, Any]:
        now_dt = _utcnow()
        now = _iso(now_dt)
        effective_next_run_at = next_run_at
        if effective_next_run_at is None and status == "active":
            effective_next_run_at = _next_run_at(recurrence, after=now_dt)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO onguard_schedules (
                    schedule_id, client_id, recurrence, command, payload,
                    labels, status, created_by, approved_by, next_run_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(schedule_id) DO UPDATE SET
                    recurrence = excluded.recurrence,
                    command = excluded.command,
                    payload = excluded.payload,
                    labels = excluded.labels,
                    status = excluded.status,
                    approved_by = excluded.approved_by,
                    next_run_at = excluded.next_run_at,
                    updated_at = excluded.updated_at
                """,
                (
                    schedule_id,
                    client_id,
                    _json(recurrence),
                    command,
                    _json(payload),
                    _json(labels),
                    status,
                    created_by,
                    approved_by,
                    effective_next_run_at,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM onguard_schedules WHERE schedule_id = ?",
                (schedule_id,),
            ).fetchone()
        return _schedule(row)

    async def update_schedule(
        self,
        *,
        schedule_id: str,
        recurrence: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        labels: list[str] | None = None,
        status: str | None = None,
        next_run_at: str | None = None,
    ) -> dict[str, Any]:
        await self.initialize()
        return await run_in_thread(
            self._update_schedule_sync,
            schedule_id,
            recurrence,
            payload,
            labels,
            status,
            next_run_at,
        )

    def _update_schedule_sync(
        self,
        schedule_id: str,
        recurrence: dict[str, Any] | None,
        payload: dict[str, Any] | None,
        labels: list[str] | None,
        status: str | None,
        next_run_at: str | None,
    ) -> dict[str, Any]:
        updates = []
        args: list[Any] = []
        if recurrence is not None:
            updates.append("recurrence = ?")
            args.append(_json(recurrence))
        if payload is not None:
            updates.append("payload = ?")
            args.append(_json(payload))
        if labels is not None:
            updates.append("labels = ?")
            args.append(_json(labels))
        if status is not None:
            updates.append("status = ?")
            args.append(status)
        if next_run_at is not None:
            updates.append("next_run_at = ?")
            args.append(next_run_at)
        elif recurrence is not None and status == "active":
            updates.append("next_run_at = ?")
            args.append(_next_run_at(recurrence))
        updates.append("updated_at = ?")
        args.append(_iso(_utcnow()))
        args.append(schedule_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE onguard_schedules SET {', '.join(updates)} WHERE schedule_id = ?",
                args,
            )
            row = conn.execute(
                "SELECT * FROM onguard_schedules WHERE schedule_id = ?",
                (schedule_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"onguard schedule not found: {schedule_id}")
        return _schedule(row)

    async def disable_schedule(self, *, schedule_id: str) -> dict[str, Any]:
        return await self.update_schedule(schedule_id=schedule_id, status="disabled")

    async def run_schedule_now(
        self,
        *,
        schedule_id: str,
        created_by: str,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        await self.initialize()
        return await run_in_thread(
            self._run_schedule_now_sync,
            schedule_id,
            created_by,
            command_id or str(uuid4()),
        )

    def _run_schedule_now_sync(
        self,
        schedule_id: str,
        created_by: str,
        command_id: str,
    ) -> dict[str, Any]:
        now = _iso(_utcnow())
        with self._connect() as conn:
            schedule = conn.execute(
                "SELECT * FROM onguard_schedules WHERE schedule_id = ?",
                (schedule_id,),
            ).fetchone()
            if schedule is None:
                raise KeyError(f"onguard schedule not found: {schedule_id}")
            if schedule["status"] != "active":
                raise RuntimeError(f"onguard schedule is not active: {schedule_id}")
            conn.execute(
                """
                INSERT INTO onguard_commands (
                    command_id, client_id, command, payload, labels,
                    provenance, status, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                """,
                (
                    command_id,
                    schedule["client_id"],
                    schedule["command"],
                    schedule["payload"],
                    schedule["labels"],
                    _json({"source": f"schedule:{schedule_id}", "run_now": True}),
                    created_by,
                    now,
                    now,
                ),
            )
            command = conn.execute(
                "SELECT * FROM onguard_commands WHERE command_id = ?",
                (command_id,),
            ).fetchone()
        return _command(command)

    async def claim_due_schedule(
        self,
        *,
        client_id: str,
        claimed_by: str,
        lease_seconds: int = 300,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        await self.initialize()
        return await run_in_thread(
            self._claim_due_schedule_sync,
            client_id,
            claimed_by,
            lease_seconds,
            now,
        )

    def _claim_due_schedule_sync(
        self,
        client_id: str,
        claimed_by: str,
        lease_seconds: int,
        now: datetime | None,
    ) -> dict[str, Any] | None:
        now_dt = now or _utcnow()
        now_iso = _iso(now_dt)
        lease_until = _iso(now_dt + timedelta(seconds=max(1, lease_seconds)))
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM onguard_schedules
                WHERE client_id = ?
                  AND status = 'active'
                  AND next_run_at IS NOT NULL
                  AND next_run_at <= ?
                ORDER BY next_run_at
                LIMIT 1
                """,
                (client_id, now_iso),
            ).fetchone()
            if row is None:
                return None
            active = conn.execute(
                """
                SELECT * FROM onguard_schedule_runs
                WHERE schedule_id = ?
                  AND status = 'claimed'
                  AND lease_until > ?
                LIMIT 1
                """,
                (row["schedule_id"], now_iso),
            ).fetchone()
            if active is not None:
                return None
            run_id = str(uuid4())
            conn.execute(
                """
                INSERT INTO onguard_schedule_runs (
                    run_id, schedule_id, client_id, status, claimed_by,
                    lease_until, run_after, started_at, created_at, updated_at
                ) VALUES (?, ?, ?, 'claimed', ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    row["schedule_id"],
                    row["client_id"],
                    claimed_by,
                    lease_until,
                    row["next_run_at"],
                    now_iso,
                    now_iso,
                    now_iso,
                ),
            )
            run = conn.execute(
                "SELECT * FROM onguard_schedule_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return _schedule_run(run)

    async def complete_schedule_run(
        self,
        *,
        run_id: str,
        result: dict[str, Any],
        artifact_ref: str | None = None,
        command_id: str | None = None,
        next_run_at: str | None = None,
    ) -> dict[str, Any]:
        return await self._finish_schedule_run(
            run_id=run_id,
            status="completed",
            result=result,
            artifact_ref=artifact_ref,
            command_id=command_id,
            error=None,
            next_run_at=next_run_at,
        )

    async def fail_schedule_run(
        self,
        *,
        run_id: str,
        result: dict[str, Any],
        error: str,
        artifact_ref: str | None = None,
        command_id: str | None = None,
        next_run_at: str | None = None,
    ) -> dict[str, Any]:
        return await self._finish_schedule_run(
            run_id=run_id,
            status="failed",
            result=result,
            artifact_ref=artifact_ref,
            command_id=command_id,
            error=error,
            next_run_at=next_run_at,
        )

    async def _finish_schedule_run(
        self,
        *,
        run_id: str,
        status: str,
        result: dict[str, Any],
        artifact_ref: str | None,
        command_id: str | None,
        error: str | None,
        next_run_at: str | None,
    ) -> dict[str, Any]:
        await self.initialize()
        return await run_in_thread(
            self._finish_schedule_run_sync,
            run_id,
            status,
            result,
            artifact_ref,
            command_id,
            error,
            next_run_at,
        )

    def _finish_schedule_run_sync(
        self,
        run_id: str,
        status: str,
        result: dict[str, Any],
        artifact_ref: str | None,
        command_id: str | None,
        error: str | None,
        next_run_at: str | None,
    ) -> dict[str, Any]:
        now = _iso(_utcnow())
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE onguard_schedule_runs
                SET status = ?, result = ?, artifact_ref = ?, command_id = ?,
                    error = ?, finished_at = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (status, _json(result), artifact_ref, command_id, error, now, now, run_id),
            )
            run = conn.execute(
                "SELECT * FROM onguard_schedule_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise KeyError(f"onguard schedule run not found: {run_id}")
            effective_next_run_at = next_run_at
            if effective_next_run_at is None:
                schedule = conn.execute(
                    "SELECT recurrence FROM onguard_schedules WHERE schedule_id = ?",
                    (run["schedule_id"],),
                ).fetchone()
                if schedule is not None:
                    effective_next_run_at = _next_run_at(_loads(schedule["recurrence"], {}))
            conn.execute(
                """
                UPDATE onguard_schedules
                SET last_run_at = ?, next_run_at = COALESCE(?, next_run_at), updated_at = ?
                WHERE schedule_id = ?
                """,
                (now, effective_next_run_at, now, run["schedule_id"]),
            )
        return _schedule_run(run)

    async def schedule_history(
        self,
        *,
        schedule_id: str | None = None,
        client_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        await self.initialize()
        return await run_in_thread(
            self._schedule_history_sync,
            schedule_id,
            client_id,
            limit,
        )

    def _schedule_history_sync(
        self,
        schedule_id: str | None,
        client_id: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        clauses = []
        args: list[Any] = []
        if schedule_id:
            clauses.append("schedule_id = ?")
            args.append(schedule_id)
        if client_id:
            clauses.append("client_id = ?")
            args.append(client_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        args.append(max(1, min(limit, 500)))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM onguard_schedule_runs{where} ORDER BY created_at DESC LIMIT ?",
                args,
            ).fetchall()
        return [_schedule_run(row) for row in rows]

    async def list_schedules(
        self,
        *,
        client_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        await self.initialize()
        return await run_in_thread(self._list_schedules_sync, client_id, status)

    def _list_schedules_sync(
        self,
        client_id: str | None,
        status: str | None,
    ) -> list[dict[str, Any]]:
        clauses = []
        args: list[str] = []
        if client_id:
            clauses.append("client_id = ?")
            args.append(client_id)
        if status:
            clauses.append("status = ?")
            args.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM onguard_schedules{where} ORDER BY schedule_id",
                args,
            ).fetchall()
        return [_schedule(row) for row in rows]


def _client(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "client_id": row["client_id"],
        "kind": row["kind"],
        "owner": row["owner"],
        "version": row["version"],
        "allowed_schedules": _loads(row["allowed_schedules"], []),
        "metadata": _loads(row["metadata"], {}),
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _config(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "config_id": row["config_id"],
        "client_id": row["client_id"],
        "schema_name": row["schema_name"],
        "payload": _loads(row["payload"], {}),
        "labels": _loads(row["labels"], []),
        "status": row["status"],
        "proposed_by": row["proposed_by"],
        "approved_by": row["approved_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _command(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "command_id": row["command_id"],
        "client_id": row["client_id"],
        "command": row["command"],
        "payload": _loads(row["payload"], {}),
        "labels": _loads(row["labels"], []),
        "provenance": _loads(row["provenance"], {}),
        "status": row["status"],
        "created_by": row["created_by"],
        "claimed_by": row["claimed_by"],
        "lease_until": row["lease_until"],
        "attempts": row["attempts"],
        "result": _loads(row["result"], None),
        "artifact_ref": row["artifact_ref"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _event(row: sqlite3.Row) -> dict[str, Any]:
    keys = set(row.keys())
    return {
        "event_id": row["event_id"],
        "client_id": row["client_id"],
        "command_id": row["command_id"],
        "schedule_id": row["schedule_id"],
        "event_type": row["event_type"],
        "payload": _loads(row["payload"], {}),
        "labels": _loads(row["labels"], []),
        "acknowledged_by": row["acknowledged_by"] if "acknowledged_by" in keys else None,
        "acknowledged_at": row["acknowledged_at"] if "acknowledged_at" in keys else None,
        "created_at": row["created_at"],
    }


def _artifact(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "artifact_id": row["artifact_id"],
        "client_id": row["client_id"],
        "command_id": row["command_id"],
        "schedule_id": row["schedule_id"],
        "session_id": row["session_id"],
        "artifact_type": row["artifact_type"],
        "payload": _loads(row["payload"], {}),
        "labels": _loads(row["labels"], []),
        "provenance": _loads(row["provenance"], {}),
        "status": row["status"],
        "created_by": row["created_by"],
        "promoted_by": row["promoted_by"],
        "deleted_at": row["deleted_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _schedule(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "schedule_id": row["schedule_id"],
        "client_id": row["client_id"],
        "recurrence": _loads(row["recurrence"], {}),
        "command": row["command"],
        "payload": _loads(row["payload"], {}),
        "labels": _loads(row["labels"], []),
        "status": row["status"],
        "created_by": row["created_by"],
        "approved_by": row["approved_by"],
        "last_run_at": row["last_run_at"],
        "next_run_at": row["next_run_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _schedule_run(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "run_id": row["run_id"],
        "schedule_id": row["schedule_id"],
        "client_id": row["client_id"],
        "command_id": row["command_id"],
        "status": row["status"],
        "claimed_by": row["claimed_by"],
        "lease_until": row["lease_until"],
        "run_after": row["run_after"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "result": _loads(row["result"], None),
        "artifact_ref": row["artifact_ref"],
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
