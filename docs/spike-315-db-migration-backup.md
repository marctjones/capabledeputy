# Spike #315 — State-DB migration + backup strategy

**Status:** resolved · **Date:** 2026-07-18 · **Blocks:** v0.57 (#321)
**Question:** replace the destructive wipe-on-schema-mismatch-or-corruption in
`session/store.py`; choose a migration approach; define snapshot-before-touch +
corruption-quarantine-not-delete; cover the other SQLite stores.

## TL;DR decision

- **Migrations:** hand-rolled **forward-only versioned migrations** — a
  dependency-free `dict[int, Callable[[Connection], None]]` runner applied in
  order inside a transaction. **No** external library (yoyo/alembic) — overkill
  for single-file local SQLite and adds a TCB dependency.
- **Snapshot-before-touch:** already shipped for the session store (#321); lift
  it into a shared `store_lifecycle` helper every store uses.
- **Corruption → QUARANTINE, never delete:** on `integrity_check` failure /
  `DatabaseError`, **move** the file to `<db>.corrupt-<ts>` (forensics
  preserved), log, recreate clean.
- **Wipe is the LAST resort, not the first:** only when there is genuinely no
  migration path (the four-axis cutover) — and even then, snapshot first.
- **Cover all stores** via the shared helper: session, memory, onguard,
  admission, overrides.

## Current state (evidence)

| Store | Versioned? | Schema-mismatch handling | Corruption handling |
|---|---|---|---|
| `session/store.py` | `SCHEMA_VERSION=9` | **wipe** (now snapshot-before-wipe, #321) | wipe (as `DatabaseError` ⇒ `_needs_wipe`) |
| `tools/native/memory.py` | none | ad-hoc `ALTER TABLE … ADD COLUMN` (additive) | none |
| `onguard/store.py` | none | `CREATE TABLE IF NOT EXISTS` only (additive) | none |
| `upstream/admission_store.py` | none | `CREATE TABLE IF NOT EXISTS` only (additive) | none |
| `policy/overrides.py` | (grant store; small) | additive | none |

So today: only the session store is versioned, and its answer to *any*
mismatch/corruption is a wipe. #321 already made that wipe **non-destructive**
(snapshot-before-wipe). The rest are additive-only — safe for column *adds*, but
they silently diverge on any incompatible change and none quarantine a corrupt
file. **This spike generalizes #321 into a shared lifecycle + a real migration
path.**

## The shared store lifecycle

A small `store/lifecycle.py` (in-TCB, dependency-free) exposes one entry point:

```python
def open_managed(
    path: Path,
    *,
    schema_version: int,
    schema_sql: str,                       # CREATE TABLE IF NOT EXISTS … (fresh DB)
    migrations: dict[int, Migration],      # {from_version: fn(conn) -> None}
) -> ManagedDb:
    ...
```

Decision order (each step logs; nothing is ever silently lost):

1. **Absent** → create clean from `schema_sql`, stamp `schema_version`.
2. **Unreadable / integrity_check fails** → **quarantine** to
   `<db>.corrupt-<ts>` (rename, don't delete), then create clean. Record the
   quarantine path.
3. **version == current** → open, done.
4. **version < current AND a migration path exists** →
   **snapshot** to `<db>.pre-migrate-v<old>.bak`, then apply
   `migrations[v], migrations[v+1], …` in order **inside a single transaction**
   (rollback on failure → leave the DB untouched, surface the error; the
   snapshot is the recovery point), then stamp the new version.
5. **version < current AND no migration path** (the four-axis cutover) →
   **snapshot** (the #321 path) then recreate clean. This is the only wipe, and
   it is loud + backed up.
6. **version > current** (a downgrade) → refuse to open (fail-closed): a newer
   DB must not be silently truncated by an older binary; quarantine + clean, or
   require an explicit `--force`.

`ManagedDb` records `last_backup_path` / `last_quarantine_path` so
`capdep doctor` (#322) can surface "your previous state was preserved at X."

## Why hand-rolled forward-only migrations

- **Single-file, single-writer, local.** No concurrent-migration coordination,
  no multi-tenant history — the heavy machinery of alembic/yoyo buys nothing.
- **In-TCB, no dependency.** The migration runner is ~40 lines and stays
  auditable; adding a migration framework to the trust boundary is a poor
  trade.
- **Forward-only** matches the product: personal, local, no downgrade support
  (rule 6 fails-closed on a newer DB). Each store owns
  `SCHEMA_VERSION` + a `migrations` dict; a schema change ships a new version +
  one migration function (usually an `ALTER TABLE ADD COLUMN`, occasionally a
  data backfill), tested against a fixture DB at the prior version.
- SQLite's transactional DDL makes step-4 atomic — a failed migration rolls back
  cleanly to the pre-migration state (and the snapshot is the belt).

## Corruption quarantine (not delete)

`integrity_check != "ok"` or a `sqlite3.DatabaseError` on open means the bytes
are untrustworthy — but not worthless (partial recovery, forensics, "what did I
lose?"). **Rename** to `<db>.corrupt-<ISO8601>` and start clean, rather than
`unlink`. This replaces the current session-store behavior of treating a
`DatabaseError` as `_needs_wipe → unlink` (a delete). Retain the last N
quarantine/backup files; a `capdep maintenance prune-backups` command reaps
older ones so they can't grow unbounded.

## Per-store adoption plan (feeds #321 completion)

1. **session/store.py** — adopt `open_managed`; register the real migration
   registry (v-1→v migrations where they exist); keep the four-axis cutover as
   the "no path" branch (rule 5), and switch corruption from wipe to quarantine
   (rule 2). (Builds directly on the shipped #321 snapshot.)
2. **memory.py** — add a `schema_version` table; formalize the ad-hoc
   `ALTER ADD COLUMN` into `migrations` (idempotence via the version stamp
   instead of try/except-on-duplicate-column).
3. **onguard/admission/overrides** — add a `schema_version` stamp + `open_managed`
   so a future incompatible change migrates instead of silently diverging, and a
   corrupt file quarantines. No behavior change today (they're at v1).

## Deliverable → #321 implementation checklist

- [ ] `store/lifecycle.py`: `open_managed` (snapshot / quarantine / migrate /
      last-resort-wipe / downgrade-refuse) + `ManagedDb`.
- [ ] Migration-runner unit tests: fixture DB at v(n-1) migrates to v(n) with
      data intact; a failing migration rolls back and leaves the DB + snapshot
      intact; a corrupt DB is quarantined (not deleted) and recreated; a
      newer-version DB is refused.
- [ ] session store: swap wipe→quarantine for corruption; register migrations;
      keep the cutover as last-resort.
- [ ] memory/onguard/admission/overrides: adopt `open_managed`.
- [ ] `capdep doctor` surfaces last backup/quarantine paths; `capdep maintenance
      prune-backups` reaps old ones.

Note: #321 already shipped the session **snapshot-before-wipe** — the first
increment of this design. #315 defines the rest (real migrations,
corruption-quarantine, the shared helper, and the other stores).
