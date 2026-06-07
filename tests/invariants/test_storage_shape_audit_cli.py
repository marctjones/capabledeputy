"""T112 — Storage-shape audit CLI behavior (SC-019).

`capdep audit storage-shape` exits 0 on a clean v8 store and
non-zero on one carrying flat-legacy rows (sessions with empty
label_state after migration).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from capabledeputy.policy.storage_audit import audit_storage_shape


def _make_clean_v6_store(tmp_path: Path) -> Path:
    """Create a minimal v8 sessions store with one well-formed row.
    Hand-rolled schema mirrors session/store._SCHEMA_SQL for the
    columns this audit cares about."""
    db = tmp_path / "store.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            label_set TEXT NOT NULL DEFAULT '[]',
            label_state TEXT NOT NULL DEFAULT '{}',
            axis_d TEXT NOT NULL DEFAULT '{}',
            purpose_handle TEXT NOT NULL DEFAULT 'unset'
        )
        """,
    )
    label_state_json = (
        '{"a":[{"category":"personal","tier":"regulated"}],'
        '"b":[{"level":"principal-direct"}]}'
    )
    conn.execute(
        "INSERT INTO sessions (id, label_state, axis_d) VALUES (?, ?, ?)",
        ("s1", label_state_json, "{}"),
    )
    conn.commit()
    conn.close()
    return db


def _make_legacy_v6_store(tmp_path: Path) -> Path:
    """A v8 store with a session whose label_state is still empty
    despite having legacy `label_set` content — i.e., the migration
    didn't fold it correctly."""
    db = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            label_set TEXT NOT NULL DEFAULT '[]',
            label_state TEXT NOT NULL DEFAULT '{}',
            axis_d TEXT NOT NULL DEFAULT '{}',
            purpose_handle TEXT NOT NULL DEFAULT 'unset'
        )
        """,
    )
    conn.execute(
        "INSERT INTO sessions (id, label_set, label_state) VALUES (?, ?, ?)",
        ("legacy", '["confidential.personal"]', '{"a":[],"b":[]}'),
    )
    conn.commit()
    conn.close()
    return db


def test_audit_passes_on_clean_v8_store(tmp_path: Path) -> None:
    db = _make_clean_v6_store(tmp_path)
    result = audit_storage_shape(db)
    assert result.is_clean
    assert result.flat_legacy_session_ids == ()


def test_audit_flags_flat_legacy_rows(tmp_path: Path) -> None:
    db = _make_legacy_v6_store(tmp_path)
    result = audit_storage_shape(db)
    assert not result.is_clean
    assert "legacy" in result.flat_legacy_session_ids
