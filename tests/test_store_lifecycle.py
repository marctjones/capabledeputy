"""#315/#321 — prepare_managed_db: non-destructive migration + backup. Covers
every branch of the decision order: fresh / opened / migrate / rollback /
quarantine-corrupt / quarantine-newer / wipe-no-path."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from capabledeputy.store import (
    RecoveryOutcome,
    StoreMigrationError,
    prepare_managed_db,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL PRIMARY KEY);
CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY);
"""


def _version(path: Path) -> int:
    con = sqlite3.connect(path)
    v = con.execute("SELECT version FROM schema_version").fetchone()[0]
    con.close()
    return v


def _seed(path: Path, version: int, ids: list[str]) -> None:
    path.unlink(missing_ok=True)
    con = sqlite3.connect(path)
    con.executescript(_SCHEMA)
    con.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
    con.executemany("INSERT INTO items (id) VALUES (?)", [(i,) for i in ids])
    con.commit()
    con.close()


def test_absent_creates_fresh(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    out = prepare_managed_db(db, schema_version=1, schema_sql=_SCHEMA)
    assert out.action == "fresh"
    assert _version(db) == 1


def test_matching_version_opens_and_preserves_data(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    _seed(db, 1, ["a", "b"])
    out = prepare_managed_db(db, schema_version=1, schema_sql=_SCHEMA)
    assert out.action == "opened"
    con = sqlite3.connect(db)
    assert {r[0] for r in con.execute("SELECT id FROM items")} == {"a", "b"}
    con.close()


def test_migration_upgrades_and_keeps_data(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    _seed(db, 1, ["a"])

    def _v1_to_v2(con: sqlite3.Connection) -> None:
        con.execute("ALTER TABLE items ADD COLUMN note TEXT DEFAULT ''")

    out = prepare_managed_db(db, schema_version=2, schema_sql=_SCHEMA, migrations={1: _v1_to_v2})
    assert out.action == "migrated" and out.from_version == 1
    assert out.backup_path is not None and out.backup_path.is_file()
    assert _version(db) == 2
    con = sqlite3.connect(db)
    assert con.execute("SELECT id, note FROM items").fetchone() == ("a", "")
    con.close()


def test_failed_migration_rolls_back_and_leaves_db_intact(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    _seed(db, 1, ["a"])

    def _bad(con: sqlite3.Connection) -> None:
        con.execute("ALTER TABLE items ADD COLUMN note TEXT")
        raise RuntimeError("boom mid-migration")

    with pytest.raises(StoreMigrationError, match="rolled back"):
        prepare_managed_db(db, schema_version=2, schema_sql=_SCHEMA, migrations={1: _bad})
    # DB is untouched: still v1, no 'note' column, and the snapshot exists.
    assert _version(db) == 1
    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(items)")}
    con.close()
    assert "note" not in cols
    assert list(tmp_path.glob("s.db.pre-migrate*"))


def test_no_migration_path_snapshots_then_wipes(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    _seed(db, 1, ["a", "b"])
    out = prepare_managed_db(db, schema_version=5, schema_sql=_SCHEMA, migrations={})
    assert out.action == "wiped-no-path" and out.from_version == 1
    assert out.backup_path is not None
    # backup still holds the old data; live DB is fresh + empty at v5.
    con = sqlite3.connect(out.backup_path)
    assert {r[0] for r in con.execute("SELECT id FROM items")} == {"a", "b"}
    con.close()
    assert _version(db) == 5
    con = sqlite3.connect(db)
    assert con.execute("SELECT count(*) FROM items").fetchone()[0] == 0
    con.close()


def test_corrupt_db_is_quarantined_not_deleted(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    db.write_bytes(b"this is not a sqlite database")
    out = prepare_managed_db(db, schema_version=1, schema_sql=_SCHEMA)
    assert out.action == "quarantined-corrupt"
    assert out.quarantine_path is not None
    assert out.quarantine_path.read_bytes() == b"this is not a sqlite database"
    assert _version(db) == 1  # fresh live DB


def test_readable_but_wrong_shape_is_quarantined(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    con = sqlite3.connect(db)  # valid sqlite, but no schema_version table
    con.execute("CREATE TABLE foreign_thing (x int)")
    con.commit()
    con.close()
    out = prepare_managed_db(db, schema_version=1, schema_sql=_SCHEMA)
    assert out.action == "quarantined-corrupt"
    assert out.quarantine_path is not None


def test_newer_version_is_quarantined_fail_closed(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    _seed(db, 9, ["a"])
    out = prepare_managed_db(db, schema_version=2, schema_sql=_SCHEMA)
    assert out.action == "quarantined-newer" and out.from_version == 9
    assert out.quarantine_path is not None
    # the newer DB is preserved, live DB is fresh at the binary's version.
    assert _version(out.quarantine_path) == 9
    assert _version(db) == 2


def test_backups_do_not_collide(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    for _ in range(3):
        _seed(db, 1, ["x"])
        prepare_managed_db(db, schema_version=5, schema_sql=_SCHEMA, migrations={})
    assert len(list(tmp_path.glob("s.db.pre-wipe*"))) == 3


def test_outcome_is_frozen_dataclass() -> None:
    import dataclasses

    o = RecoveryOutcome("fresh")
    with pytest.raises(dataclasses.FrozenInstanceError):
        o.action = "x"  # type: ignore[misc]
