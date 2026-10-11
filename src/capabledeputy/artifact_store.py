"""Internal durable result storage, not a client-facing authorization API.

Callers must obtain source labels from trusted session state. Reading this
store is not permission to expose content, metadata, or attach it elsewhere.
"""

from __future__ import annotations

import json
import sqlite3
import stat
from contextlib import closing, suppress
from dataclasses import replace
from pathlib import Path

from capabledeputy.artifacts import ArtifactError, TypedArtifact
from capabledeputy.policy.labels import LabelState, label_join


class ArtifactStore:
    """Append-only immutable versions keyed by source task and artifact hash.

    A separate database avoids coupling results to live-session retention.
    There is deliberately no relabel, export, release, or public list method.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation establishes owner-only access before writing data.
        if not path.exists():
            with suppress(FileExistsError):
                path.touch(mode=0o600, exist_ok=False)
        if (
            path.is_symlink()
            or not stat.S_ISREG(path.stat().st_mode)
            or path.stat().st_mode & 0o077
        ):
            raise ArtifactError("artifact database must be an owner-only regular file")
        self.path = path
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS results (
                source_session TEXT NOT NULL,
                source_task TEXT NOT NULL,
                digest TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (source_session, source_task, digest)
            )""")

    def save(
        self,
        artifact: TypedArtifact,
        *,
        source_session: str,
        source_task: str,
        source_labels: LabelState,
    ) -> str:
        """Join trusted source labels; commit before returning a stable digest."""
        if not source_session.strip() or not source_task.strip():
            raise ArtifactError("source session and task are required")
        if not isinstance(source_labels, LabelState):
            raise ArtifactError("trusted source labels are required")
        frozen = replace(artifact, labels=label_join(source_labels, artifact.labels))
        digest = frozen.sha256
        payload = json.dumps(frozen.to_dict(), sort_keys=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO results VALUES (?, ?, ?, ?)",
                (source_session, source_task, digest, payload),
            )
        return digest

    def read_internal(
        self,
        *,
        source_session: str,
        source_task: str,
        digest: str,
    ) -> TypedArtifact:
        """Trusted internal lookup; external exposure requires a policy gate."""
        with closing(sqlite3.connect(self.path)) as db, db:
            row = db.execute(
                "SELECT payload FROM results WHERE source_session=? AND source_task=? AND digest=?",
                (source_session, source_task, digest),
            ).fetchone()
        if row is None:
            raise ArtifactError("artifact not found")
        artifact = TypedArtifact.from_dict(json.loads(row[0]))
        if artifact.sha256 != digest:
            raise ArtifactError("stored artifact digest mismatch")
        return artifact
