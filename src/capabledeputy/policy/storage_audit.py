"""Storage-shape audit (003 T016, FR-045, SC-019).

The v0.9 spec mandates axis orthogonality is *observably* expressed in
storage — not encoded into prefixed strings, not implicit in the
schema. This helper verifies that every persisted sessions row carries
the four axis columns with parseable JSON shapes.

Surfaces as the body of `capdep audit storage-shape` (T004 -> wired
here in T016).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StorageShapeReport:
    """Result of an audit_storage_shape() call.

    `n_total` is the total session rows examined. `bad_rows` lists
    (session_id, reason) for any row failing the *structural* shape
    check (parseable JSON, right types). `flat_legacy_session_ids`
    lists rows that pass the structural check but still carry the
    flat-legacy pattern (non-empty label_set + empty axis_a + empty
    axis_b + empty axis_d) — i.e., the v5→v6 converter missed them.
    SC-019 passes iff BOTH lists are empty.
    """

    n_total: int = 0
    bad_rows: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    flat_legacy_session_ids: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.bad_rows and not self.flat_legacy_session_ids

    @property
    def is_clean(self) -> bool:
        """Alias for `ok` — matches the noun the CLI report uses."""
        return self.ok


def audit_storage_shape(db_path: Path) -> StorageShapeReport:
    """Open the sessions store and verify FR-045 shape on every row.

    Checks per row:
    - label_state column is parseable JSON object.
    - axis_d column is parseable JSON object.
    - purpose_handle column is a non-empty string.
    - reference_handles is parseable JSON object.

    The presence/absence of meaningful label content is NOT checked
    here — an empty label_state is a valid shape (FR-045 is structural, not
    semantic). Empty labels only fail at decide() when the resolver
    can't find a tier — and that's a Phase-3 concern, not a storage
    concern.
    """
    if not db_path.exists():
        return StorageShapeReport(n_total=0, bad_rows=())

    bad: list[tuple[str, str]] = []
    flat_legacy: list[str] = []
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        # Probe schema — older test stores may not have all columns.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        has_label_set = "label_set" in cols
        has_label_state = "label_state" in cols
        has_purpose_handle = "purpose_handle" in cols
        has_reference_handles = "reference_handles" in cols
        select_cols = ["id", "axis_d"]
        if has_label_state:
            select_cols.append("label_state")
        if has_label_set:
            select_cols.append("label_set")
        if has_purpose_handle:
            select_cols.append("purpose_handle")
        if has_reference_handles:
            select_cols.append("reference_handles")
        cursor = conn.execute(f"SELECT {', '.join(select_cols)} FROM sessions")
        rows = cursor.fetchall()

    for row in rows:
        sid = row["id"]
        label_state: dict[str, list[dict[str, Any]]] = {"a": [], "b": []}

        if has_label_state:
            try:
                label_state = json.loads(row["label_state"])
                if not isinstance(label_state, dict):
                    bad.append((sid, "label_state is not an object"))
                    continue
                # Validate structure: should have "a" and "b" keys
                if "a" not in label_state or "b" not in label_state:
                    bad.append((sid, "label_state missing a or b keys"))
                    continue
                if not isinstance(label_state.get("a"), list):
                    bad.append((sid, "label_state.a is not a list"))
                    continue
                if not isinstance(label_state.get("b"), list):
                    bad.append((sid, "label_state.b is not a list"))
                    continue
            except (json.JSONDecodeError, TypeError):
                bad.append((sid, "label_state is not parseable JSON"))
                continue

        try:
            axis_d = json.loads(row["axis_d"])
            if not isinstance(axis_d, dict):
                bad.append((sid, "axis_d is not an object"))
                continue
        except (json.JSONDecodeError, TypeError):
            bad.append((sid, "axis_d is not parseable JSON"))
            continue

        if has_purpose_handle and (
            not isinstance(row["purpose_handle"], str) or not row["purpose_handle"]
        ):
            bad.append((sid, "purpose_handle is empty or non-string"))
            continue

        if has_reference_handles:
            try:
                handles = json.loads(row["reference_handles"])
                if not isinstance(handles, dict):
                    bad.append((sid, "reference_handles is not an object"))
                    continue
            except (json.JSONDecodeError, TypeError):
                bad.append((sid, "reference_handles is not parseable JSON"))
                continue

        # SC-019 semantic check: non-empty label_set + empty label_state
        # ⇒ flat-legacy row that escaped the v5→v6 converter.
        if has_label_set and has_label_state:
            try:
                legacy = json.loads(row["label_set"])
            except (json.JSONDecodeError, TypeError):
                legacy = []
            label_a = label_state.get("a", [])
            label_b = label_state.get("b", [])
            if (
                isinstance(legacy, list)
                and len(legacy) > 0
                and len(label_a) == 0
                and len(label_b) == 0
                and len(axis_d) == 0
            ):
                flat_legacy.append(sid)

    return StorageShapeReport(
        n_total=len(rows),
        bad_rows=tuple(bad),
        flat_legacy_session_ids=tuple(flat_legacy),
    )
