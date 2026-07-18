"""#321 — non-destructive DB init: a state DB at a different schema version is
recreated clean BUT snapshotted first (no silent, backup-less data loss)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import anyio

from capabledeputy.session.store import SCHEMA_VERSION, SessionStore


def _write_db_at_version(path: Path, version: int) -> None:
    path.unlink(missing_ok=True)
    for sc in ("-wal", "-shm"):
        path.with_name(path.name + sc).unlink(missing_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE schema_version (version INTEGER NOT NULL PRIMARY KEY)")
    con.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
    con.execute("CREATE TABLE sessions (id TEXT)")
    con.execute("INSERT INTO sessions (id) VALUES ('old-session')")
    con.commit()
    con.close()


def test_wrong_version_is_backed_up_then_wiped(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    _write_db_at_version(db, SCHEMA_VERSION - 1)

    store = SessionStore(db)
    anyio.run(store.initialize)

    # A backup was taken and it still holds the OLD data.
    assert store.last_backup_path is not None
    assert store.last_backup_path.is_file()
    con = sqlite3.connect(store.last_backup_path)
    assert con.execute("SELECT id FROM sessions").fetchone()[0] == "old-session"
    con.close()

    # The live DB is fresh at the current schema version.
    con = sqlite3.connect(db)
    assert con.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION
    con.close()


def test_matching_version_is_not_touched(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    store = SessionStore(db)
    anyio.run(store.initialize)  # creates clean at current version
    assert store.last_backup_path is None

    store2 = SessionStore(db)
    anyio.run(store2.initialize)  # already current -> no wipe, no backup
    assert store2.last_backup_path is None


def test_corrupt_db_is_quarantined_not_deleted(tmp_path: Path) -> None:
    # #315 — corruption now QUARANTINES (rename, forensics preserved) rather than
    # the old copy-then-delete. The live DB is recreated clean.
    db = tmp_path / "state.db"
    db.write_bytes(b"not a sqlite database at all")
    store = SessionStore(db)
    anyio.run(store.initialize)
    assert store.last_recovery_action == "quarantined-corrupt"
    assert store.last_quarantine_path is not None
    assert store.last_quarantine_path.read_bytes() == b"not a sqlite database at all"


def test_backups_do_not_collide(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    for _ in range(3):
        _write_db_at_version(db, SCHEMA_VERSION - 1)
        anyio.run(SessionStore(db).initialize)
    backups = list(tmp_path.glob("state.db.pre-wipe*"))
    assert len(backups) == 3  # no overwrite
