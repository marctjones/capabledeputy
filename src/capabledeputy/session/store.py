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

SCHEMA_VERSION = 6

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
    -- Cookbook Pattern ⑥ — STRICT (default) | SHADOW.
    enforcement_mode TEXT NOT NULL DEFAULT 'strict',
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


# 003 T011 — legacy label_set -> axis_a/axis_b mapping (FR-024 forward-
# only). REGULATED chosen for the confidential.* legacy values: preserves
# the "sensitive, needs approval" semantics without escalating to
# RESTRICTED (which would suddenly require Pattern ③ at spawn — a
# usability regression for migrated sessions). EGRESS_* values are
# effect-class signals and stay in the legacy label_set column for
# audit; they don't map to axis_a/axis_b.
_LEGACY_TO_AXIS_A: dict[str, dict[str, str]] = {
    "confidential.health": {"category": "health", "tier": "regulated"},
    "confidential.financial": {"category": "financial", "tier": "regulated"},
    "confidential.personal": {"category": "personal", "tier": "regulated"},
}
_LEGACY_TO_AXIS_B: dict[str, dict[str, object]] = {
    "untrusted.external": {"level": "external-untrusted", "integrity_floor": False},
    "untrusted.user_input": {"level": "external-untrusted", "integrity_floor": False},
    "trusted.user_direct": {"level": "principal-direct", "integrity_floor": False},
}

# 003 T047 — legacy trust prefix -> Axis-D initiator+authentication.
# We have no faithful record of the actual initiator on legacy rows,
# so the migration picks conservative values derived from the trust
# prefix that *was* recorded:
#   * trusted.user_direct  ⇒ principal:legacy-migration / device-bound
#   * untrusted.*          ⇒ external:legacy-untrusted / none
# Most-restrictive composition (FR-024): if a session carries any
# `untrusted.*` label, the untrusted axis_d wins regardless of
# whether `trusted.user_direct` is also present — the worst-case
# initiator drives the migration.
_AXIS_D_TRUSTED_DEFAULT: dict[str, object] = {
    "initiator": "principal:legacy-migration",
    "authentication": "device-bound",
    "counterparty": None,
    "relationship_group_ids": [],
    "expectedness": "anomalous",
    "reversibility": {"degree": "irreversible", "agent": "external"},
}
_AXIS_D_UNTRUSTED_DEFAULT: dict[str, object] = {
    "initiator": "external:legacy-untrusted",
    "authentication": "none",
    "counterparty": None,
    "relationship_group_ids": [],
    "expectedness": "anomalous",
    "reversibility": {"degree": "irreversible", "agent": "external"},
}
_LEGACY_TRUSTED_LABELS = frozenset({"trusted.user_direct"})
_LEGACY_UNTRUSTED_LABELS = frozenset({"untrusted.external", "untrusted.user_input"})


def _convert_legacy_label_set(label_set_json: str) -> tuple[str, str, str]:
    """T011/T047 legacy converter: read the v0.7 label_set JSON list and
    return (axis_a_json, axis_b_json, axis_d_json) for backfill into
    the new columns. Most-restrictive position per FR-024 — REGULATED
    for confidential.*, EXTERNAL_UNTRUSTED for untrusted.*, and an
    Axis-D initiator/authentication derived from the trust prefix.
    Idempotent: re-running on already-converted data yields equivalent
    output. Returns ('[]', '[]', '{}') when nothing maps."""
    try:
        labels = json.loads(label_set_json or "[]")
    except json.JSONDecodeError:
        return "[]", "[]", "{}"

    axis_a_entries: list[dict[str, object]] = []
    axis_b_entries: list[dict[str, object]] = []
    seen_categories: set[str] = set()
    seen_levels: set[str] = set()
    saw_trusted = False
    saw_untrusted = False

    for label in labels:
        if label in _LEGACY_TO_AXIS_A:
            spec = _LEGACY_TO_AXIS_A[label]
            cat = spec["category"]
            if cat in seen_categories:
                continue
            seen_categories.add(cat)
            axis_a_entries.append(
                {
                    "category": cat,
                    "tier": spec["tier"],
                    "risk_ids": [],
                    "assignment_provenance": "legacy-migration",
                },
            )
        elif label in _LEGACY_TO_AXIS_B:
            spec_b = _LEGACY_TO_AXIS_B[label]
            lvl = str(spec_b["level"])
            if lvl in seen_levels:
                continue
            seen_levels.add(lvl)
            axis_b_entries.append(spec_b)
        if label in _LEGACY_TRUSTED_LABELS:
            saw_trusted = True
        if label in _LEGACY_UNTRUSTED_LABELS:
            saw_untrusted = True
        # EGRESS_* and unknown labels: skip; remain in legacy label_set.

    # T047 — Axis-D derivation. Most-restrictive: untrusted wins if
    # any untrusted label is present, even alongside trusted.
    if saw_untrusted:
        axis_d_json = json.dumps(_AXIS_D_UNTRUSTED_DEFAULT)
    elif saw_trusted:
        axis_d_json = json.dumps(_AXIS_D_TRUSTED_DEFAULT)
    else:
        axis_d_json = "{}"

    return json.dumps(axis_a_entries), json.dumps(axis_b_entries), axis_d_json


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
                # Defensive idempotent backfill — a db can land at v6
                # via several intermediate states (an early-v6 build
                # before axis_a was added; a v5 db that got its version
                # stamp bumped without the column ALTERs running).
                # Re-apply ALL the v6 ALTERs unconditionally; duplicate-
                # column errors are caught + ignored.
                _apply_v6_idempotent_alters(conn)
                return
            if current in (1, 2, 3, 4, 5):
                if current == 1:
                    # v1 → v2: tool_aliasing + prefer_programmatic
                    for col in ("tool_aliasing", "prefer_programmatic"):
                        try:
                            conn.execute(
                                f"ALTER TABLE sessions ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0",
                            )
                        except sqlite3.OperationalError as e:
                            if "duplicate column" not in str(e).lower():
                                raise
                # v2..v5 → v6: apply all the column ALTERs idempotently,
                # then backfill legacy label_set rows.
                _apply_v6_idempotent_alters(conn)
                # T011/T047 legacy converter: backfill axis_a/axis_b/axis_d
                # from any existing label_set rows that haven't been
                # converted yet.
                rows_to_convert = conn.execute(
                    "SELECT id, label_set FROM sessions "
                    "WHERE axis_a = '[]' AND axis_b = '[]' AND axis_d = '{}'",
                ).fetchall()
                for row in rows_to_convert:
                    axis_a_json, axis_b_json, axis_d_json = _convert_legacy_label_set(
                        row["label_set"],
                    )
                    if axis_a_json == "[]" and axis_b_json == "[]" and axis_d_json == "{}":
                        continue
                    conn.execute(
                        "UPDATE sessions SET axis_a = ?, axis_b = ?, axis_d = ? WHERE id = ?",
                        (axis_a_json, axis_b_json, axis_d_json, row["id"]),
                    )
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
                    tool_aliasing, prefer_programmatic, used_kinds, cap_uses,
                    revoked_audit_ids,
                    axis_a, axis_b, axis_d, purpose_handle,
                    reference_handles, risk_preference_at_spawn,
                    effective_isolation_region_id, enforcement_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?, ?, ?)
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
                    enforcement_mode = excluded.enforcement_mode
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
        },
    )


def _apply_v6_idempotent_alters(conn: sqlite3.Connection) -> None:
    """Apply the v6 schema ALTERs idempotently — duplicate-column errors
    are caught + ignored. Safe to call on:
      - a freshly-created v6 db (no-op, every column already exists)
      - a v5 db being upgraded (all columns added)
      - an early-v6 db that was stamped before some columns existed
        (the missing columns get added; the rest no-op)

    Centralizes the column list so the same set is applied from both
    the "upgrading from v1..v5" branch AND the defensive "already
    at v6 but maybe inconsistent" branch in _initialize_sync.
    """
    for col, default in (
        ("used_kinds", "'[]'"),
        ("cap_uses", "'{}'"),
        ("revoked_audit_ids", "'[]'"),
        ("axis_a", "'[]'"),
        ("axis_b", "'[]'"),
        ("axis_d", "'{}'"),
        ("purpose_handle", "'unset'"),
        ("reference_handles", "'{}'"),
        ("risk_preference_at_spawn", "'cautious'"),
    ):
        try:
            conn.execute(
                f"ALTER TABLE sessions ADD COLUMN {col} TEXT NOT NULL DEFAULT {default}",
            )
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise
    for col in ("effective_isolation_region_id", "clearance_profile_id"):
        try:
            conn.execute(
                f"ALTER TABLE sessions ADD COLUMN {col} TEXT NULL",
            )
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise
    # Cookbook Pattern ⑥ — enforcement_mode is per-session and the
    # idempotent-ALTER pattern lets legacy sessions deserialize as
    # STRICT (matching pre-Pattern-⑥ behavior).
    try:
        conn.execute(
            "ALTER TABLE sessions ADD COLUMN enforcement_mode TEXT NOT NULL DEFAULT 'strict'",
        )
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            raise
    try:
        conn.execute(
            "ALTER TABLE override_grants ADD COLUMN state TEXT NOT NULL DEFAULT 'active'",
        )
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            raise
