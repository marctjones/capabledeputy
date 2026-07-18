"""Non-destructive SQLite store lifecycle (#315 design, #321 completion).

Replaces the "wipe-on-any-mismatch-or-corruption, no backup" pattern with a
single decision order that NEVER silently loses data:

  absent            -> create clean
  corrupt/unknown   -> QUARANTINE (rename to .corrupt-<ts>, never delete) + create clean
  version == target -> open (ensure additive schema)
  version <  target -> if a migration path exists: SNAPSHOT then migrate in a
                       transaction (rollback-safe); else SNAPSHOT then wipe (the
                       last-resort cutover)
  version >  target -> QUARANTINE the newer DB (fail-closed: an older binary must
                       not truncate a newer schema) + create clean

Migrations are hand-rolled, forward-only, dependency-free: a
`dict[int, Migration]` where `migrations[v]` upgrades a DB at version `v` to
`v+1`. In-TCB, ~one screen of code — no alembic/yoyo.
"""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

Migration = Callable[[sqlite3.Connection], None]

_SIDE_CARS = ("-wal", "-shm")


class StoreMigrationError(RuntimeError):
    """A migration failed or a required migration step is missing. The DB is left
    untouched (the transaction rolled back) and the snapshot is the recovery
    point — fail-closed rather than run on a half-migrated DB."""


@dataclass(frozen=True)
class RecoveryOutcome:
    """What `prepare_managed_db` did. `backup_path`/`quarantine_path` point at
    preserved prior state (for `capdep doctor` to surface)."""

    action: (
        str  # "fresh"|"opened"|"migrated"|"quarantined-corrupt"|"quarantined-newer"|"wiped-no-path"
    )
    backup_path: Path | None = None
    quarantine_path: Path | None = None
    from_version: int | None = None


_CORRUPT = object()  # sentinel: file exists but is not a readable SQLite DB


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _non_colliding(base: Path) -> Path:
    if not base.exists():
        return base
    n = 1
    while (alt := base.with_name(f"{base.name}.{n}")).exists():
        n += 1
    return alt


def _read_version(path: Path) -> object:
    """Return the stored schema version (int), None (readable but no
    schema_version row/table), or _CORRUPT (unreadable / integrity failure)."""
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return _CORRUPT
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            return _CORRUPT
        row = con.execute("SELECT version FROM schema_version").fetchone()
    except sqlite3.DatabaseError:
        # Corrupt file, OR a valid DB with no schema_version table. Distinguish:
        try:
            con.execute("SELECT 1").fetchone()
            return None  # readable DB, just not our shape
        except sqlite3.DatabaseError:
            return _CORRUPT
    finally:
        con.close()
    return int(row[0]) if row is not None else None


def _copy(path: Path, dest: Path) -> None:
    shutil.copy2(path, dest)
    for sc in _SIDE_CARS:
        src = path.with_name(path.name + sc)
        if src.exists():
            shutil.copy2(src, dest.with_name(dest.name + sc))


def _move(path: Path, dest: Path) -> None:
    shutil.move(str(path), str(dest))
    for sc in _SIDE_CARS:
        src = path.with_name(path.name + sc)
        if src.exists():
            shutil.move(str(src), str(dest.with_name(dest.name + sc)))


def _snapshot(path: Path, tag: str) -> Path:
    dest = _non_colliding(path.with_name(f"{path.name}.{tag}"))
    _copy(path, dest)
    return dest


def _quarantine(path: Path, tag: str) -> Path:
    dest = _non_colliding(path.with_name(f"{path.name}.{tag}"))
    _move(path, dest)
    return dest


def _wipe(path: Path) -> None:
    path.unlink(missing_ok=True)
    for sc in _SIDE_CARS:
        path.with_name(path.name + sc).unlink(missing_ok=True)


def _create_fresh(path: Path, schema_sql: str, version: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    try:
        con.executescript(schema_sql)
        if con.execute("SELECT version FROM schema_version").fetchone() is None:
            con.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
        con.commit()
    finally:
        con.close()


def _has_migration_path(migrations: dict[int, Migration], frm: int, to: int) -> bool:
    return all(v in migrations for v in range(frm, to))


def _run_migrations(
    path: Path,
    frm: int,
    to: int,
    migrations: dict[int, Migration],
) -> None:
    con = sqlite3.connect(str(path))
    try:
        con.execute("BEGIN")
        for v in range(frm, to):
            migrations[v](con)
        con.execute("UPDATE schema_version SET version = ?", (to,))
        con.execute("COMMIT")
    except BaseException as e:
        con.execute("ROLLBACK")
        raise StoreMigrationError(f"migration {frm}->{to} failed and was rolled back: {e}") from e
    finally:
        con.close()


def prepare_managed_db(
    path: Path,
    *,
    schema_version: int,
    schema_sql: str,
    migrations: dict[int, Migration] | None = None,
) -> RecoveryOutcome:
    """Bring the DB at `path` to `schema_version` non-destructively, and return
    what happened. After this returns the file is at the current schema; the
    caller opens its own connections as usual."""
    migrations = migrations or {}
    if not path.exists():
        _create_fresh(path, schema_sql, schema_version)
        return RecoveryOutcome("fresh")

    version = _read_version(path)

    if version is _CORRUPT or version is None:
        q = _quarantine(path, f"corrupt-{_timestamp()}")
        _create_fresh(path, schema_sql, schema_version)
        return RecoveryOutcome("quarantined-corrupt", quarantine_path=q)

    assert isinstance(version, int)
    if version == schema_version:
        _create_fresh(path, schema_sql, schema_version)  # ensure additive schema (idempotent)
        return RecoveryOutcome("opened")

    if version > schema_version:
        # Fail-closed: an older binary must not truncate a newer schema.
        q = _quarantine(path, f"newer-v{version}-{_timestamp()}")
        _create_fresh(path, schema_sql, schema_version)
        return RecoveryOutcome("quarantined-newer", quarantine_path=q, from_version=version)

    # version < schema_version
    if _has_migration_path(migrations, version, schema_version):
        backup = _snapshot(path, f"pre-migrate-v{version}-{_timestamp()}")
        _run_migrations(path, version, schema_version, migrations)
        return RecoveryOutcome("migrated", backup_path=backup, from_version=version)

    # No migration path — last-resort wipe, snapshot first.
    backup = _snapshot(path, f"pre-wipe-v{version}-{_timestamp()}")
    _wipe(path)
    _create_fresh(path, schema_sql, schema_version)
    return RecoveryOutcome("wiped-no-path", backup_path=backup, from_version=version)
