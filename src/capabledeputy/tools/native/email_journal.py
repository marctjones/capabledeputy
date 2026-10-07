"""Durable SMTP operation claims, never a grant of authority to send.

Call ``claim`` only inside the authorized actuator. A unique draft/version can
be submitted at most once by this application. SMTP itself cannot guarantee
exactly-once delivery: an interrupted submission must remain outcome_unknown.
The daemon calls ``recover_submitting`` once at exclusive startup, before
accepting requests; opening another journal reader must not recover live work.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import make_msgid
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

TERMINAL_STATUSES = frozenset({"smtp_accepted", "failed_before_submission", "outcome_unknown"})
_SELECT = """
    SELECT email_operations.*, email_reconciliations.outcome AS reconciliation_outcome,
        email_reconciliations.reason AS reconciliation_reason,
        email_reconciliations.decided_by AS reconciled_by, email_reconciliations.reconciled_at
    FROM email_operations LEFT JOIN email_reconciliations
        ON email_operations.id = email_reconciliations.operation_id
"""


@dataclass(frozen=True)
class EmailOperation:
    id: UUID
    session_id: UUID
    draft_id: UUID
    draft_version: int
    envelope: dict[str, Any]
    envelope_hash: str
    account_fingerprint: str
    message_id: str
    status: str
    detail: str
    created_at: datetime
    updated_at: datetime
    reconciliation_outcome: str | None = None
    reconciliation_reason: str = ""
    reconciled_by: str = ""
    reconciled_at: datetime | None = None


def _operation(row: sqlite3.Row) -> EmailOperation:
    return EmailOperation(
        id=UUID(row["id"]),
        session_id=UUID(row["session_id"]),
        draft_id=UUID(row["draft_id"]),
        draft_version=row["draft_version"],
        envelope=json.loads(row["envelope"]),
        envelope_hash=row["envelope_hash"],
        account_fingerprint=row["account_fingerprint"],
        message_id=row["message_id"],
        status=row["status"],
        detail=row["detail"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        reconciliation_outcome=row["reconciliation_outcome"],
        reconciliation_reason=row["reconciliation_reason"] or "",
        reconciled_by=row["reconciled_by"] or "",
        reconciled_at=(
            datetime.fromisoformat(row["reconciled_at"]) if row["reconciled_at"] else None
        ),
    )


class EmailOperationJournal:
    """Transactional, cross-instance claims with immutable reviewed content.

    Session authorization belongs to the caller; storage is daemon-owned.
    Failed and unknown operations are terminal too. An unknown outcome blocks
    the entire draft until explicit human reconciliation confirms it was not
    sent. Only a newer reviewed version may then send, never an old operation.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(db_path) if db_path else ":memory:", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._db:
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS email_operations (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    draft_id TEXT NOT NULL,
                    draft_version INTEGER NOT NULL CHECK(draft_version > 0),
                    envelope TEXT NOT NULL,
                    envelope_hash TEXT NOT NULL,
                    account_fingerprint TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN (
                        'submitting', 'smtp_accepted', 'failed_before_submission', 'outcome_unknown'
                    )),
                    detail TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(draft_id, draft_version)
                )
            """)
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS email_reconciliations (
                    operation_id TEXT PRIMARY KEY REFERENCES email_operations(id),
                    outcome TEXT NOT NULL CHECK(outcome IN ('smtp_accepted', 'confirmed_not_sent')),
                    reason TEXT NOT NULL,
                    decided_by TEXT NOT NULL,
                    reconciled_at TEXT NOT NULL
                )
            """)

    def claim(
        self,
        *,
        session_id: UUID,
        draft_id: UUID,
        draft_version: int,
        envelope: dict[str, Any],
        account_fingerprint: str,
        message_id: str | None = None,
        operation_id: UUID | None = None,
    ) -> tuple[EmailOperation, bool]:
        if (
            isinstance(draft_version, bool)
            or not isinstance(draft_version, int)
            or draft_version < 1
        ):
            raise ValueError("draft_version must be a positive integer")
        serialized = json.dumps(envelope, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            row = self._db.execute(
                _SELECT + " WHERE draft_id = ? AND draft_version = ?",
                (str(draft_id), draft_version),
            ).fetchone()
            if row is not None:
                existing = _operation(row)
                if (
                    existing.session_id != session_id
                    or existing.envelope_hash != digest
                    or existing.account_fingerprint != account_fingerprint
                    or (message_id is not None and existing.message_id != message_id)
                ):
                    raise ValueError("This draft version already has a different frozen operation")
                return existing, False
            prior = self._db.execute(_SELECT + " WHERE draft_id = ?", (str(draft_id),)).fetchall()
            if any(self._blocks(row) for row in prior):
                raise ValueError(
                    "Draft has a submitted or unresolved operation; reconcile before a new review"
                )
            if any(
                row["draft_version"] >= draft_version or row["session_id"] != str(session_id)
                for row in prior
            ):
                raise ValueError("A retry requires a newer draft version in the same session")
            now = datetime.now(UTC).isoformat()
            identity = operation_id or uuid4()
            self._db.execute(
                "INSERT INTO email_operations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(identity),
                    str(session_id),
                    str(draft_id),
                    draft_version,
                    serialized,
                    digest,
                    account_fingerprint,
                    message_id or make_msgid(),
                    "submitting",
                    "",
                    now,
                    now,
                ),
            )
            row = self._db.execute(_SELECT + " WHERE id = ?", (str(identity),)).fetchone()
            return _operation(row), True

    def finish(self, operation_id: UUID, status: str, detail: str = "") -> EmailOperation:
        if status not in TERMINAL_STATUSES:
            raise ValueError("An operation can only finish with a terminal SMTP status")
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            row = self._db.execute(_SELECT + " WHERE id = ?", (str(operation_id),)).fetchone()
            if row is None:
                raise KeyError(operation_id)
            if row["status"] != "submitting":
                if row["status"] != status:
                    raise ValueError("A terminal email operation cannot be replayed or changed")
                return _operation(row)
            self._db.execute(
                "UPDATE email_operations SET status = ?, detail = ?, updated_at = ? WHERE id = ?",
                (status, detail, datetime.now(UTC).isoformat(), str(operation_id)),
            )
            row = self._db.execute(_SELECT + " WHERE id = ?", (str(operation_id),)).fetchone()
            return _operation(row)

    def get(self, operation_id: UUID) -> EmailOperation | None:
        with self._lock:
            row = self._db.execute(_SELECT + " WHERE id = ?", (str(operation_id),)).fetchone()
            return _operation(row) if row is not None else None

    def for_draft(self, draft_id: UUID, draft_version: int) -> EmailOperation | None:
        with self._lock:
            row = self._db.execute(
                _SELECT + " WHERE draft_id = ? AND draft_version = ?",
                (str(draft_id), draft_version),
            ).fetchone()
            return _operation(row) if row is not None else None

    def all(self, session_id: UUID | None = None) -> list[EmailOperation]:
        with self._lock:
            if session_id is None:
                rows = self._db.execute(_SELECT + " ORDER BY created_at DESC")
            else:
                rows = self._db.execute(
                    _SELECT + " WHERE session_id = ? ORDER BY created_at DESC",
                    (str(session_id),),
                )
            return [_operation(row) for row in rows]

    @staticmethod
    def _blocks(row: sqlite3.Row) -> bool:
        return row["status"] in {"submitting", "smtp_accepted"} or (
            row["status"] == "outcome_unknown"
            and row["reconciliation_outcome"] != "confirmed_not_sent"
        )

    def blocks_draft(self, draft_id: UUID) -> bool:
        """Check every version; this check also runs atomically inside claim."""
        with self._lock:
            rows = self._db.execute(_SELECT + " WHERE draft_id = ?", (str(draft_id),))
            return any(self._blocks(row) for row in rows)

    def reconcile(
        self,
        operation_id: UUID,
        outcome: str,
        *,
        reason: str,
        decided_by: str,
    ) -> EmailOperation:
        """Record operator evidence once, without changing the original outcome.

        The caller authenticates the operator and verifies explicit confirmation.
        This method never sends, changes an old claim, or grants send authority.
        """
        if outcome not in {"smtp_accepted", "confirmed_not_sent"}:
            raise ValueError(
                "Reconciliation must confirm SMTP acceptance or that mail was not sent"
            )
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or not isinstance(decided_by, str)
            or not decided_by.strip()
        ):
            raise ValueError("Reconciliation requires a reason and an identified operator")
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            row = self._db.execute(_SELECT + " WHERE id = ?", (str(operation_id),)).fetchone()
            if row is None:
                raise KeyError(operation_id)
            if row["status"] != "outcome_unknown":
                raise ValueError("Only an unknown SMTP outcome can be reconciled")
            if row["reconciliation_outcome"] is not None:
                if (
                    row["reconciliation_outcome"],
                    row["reconciliation_reason"],
                    row["reconciled_by"],
                ) != (outcome, reason, decided_by):
                    raise ValueError("Reconciliation evidence is immutable")
                return _operation(row)
            self._db.execute(
                "INSERT INTO email_reconciliations VALUES (?, ?, ?, ?, ?)",
                (str(operation_id), outcome, reason, decided_by, datetime.now(UTC).isoformat()),
            )
            row = self._db.execute(_SELECT + " WHERE id = ?", (str(operation_id),)).fetchone()
            return _operation(row)

    def recover_submitting(self) -> int:
        with self._lock, self._db:
            result = self._db.execute(
                "UPDATE email_operations SET status = 'outcome_unknown', detail = ?, "
                "updated_at = ? WHERE status = 'submitting'",
                (
                    "Daemon stopped during submission; SMTP acceptance is unknown. Do not replay.",
                    datetime.now(UTC).isoformat(),
                ),
            )
            return result.rowcount

    def close(self) -> None:
        with self._lock:
            self._db.close()
