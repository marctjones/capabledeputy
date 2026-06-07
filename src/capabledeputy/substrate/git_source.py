"""Git-backed `SourcePort` provider (003 US6 T075 / FR-048).

Canonicalizes write/read targets to stable, repo-relative ids so the TCB
audits an authoritative destination identity rather than the raw string
the model typed. Fail-closed: a target that escapes the repository root
raises rather than returning a guess.

Modular by design: one adapter behind the port. Other source substrates
(Gmail, SharePoint, S3, …) implement the same `SourcePort` ABC and
register via `get_source_port`.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from capabledeputy.substrate.source_port import SourcePort


class SourcePortError(RuntimeError):
    """Fail-closed canonicalization failure (FR-048) — e.g. the target
    cannot be resolved within the source, or escapes its root."""


class GitSourcePort(SourcePort):
    """`SourcePort` over a local git work tree. Canonical ids have the
    form ``git:<repo-relative-posix-path>`` — stable and independent of
    the absolute path or the scheme the caller passed."""

    surfaces_destination_id: bool = True

    def __init__(self, repo_root: Path | str, *, git_bin: str | None = None) -> None:
        git = git_bin or shutil.which("git") or "git"
        root = Path(repo_root).resolve()
        try:
            proc = subprocess.run(
                [git, "-C", str(root), "rev-parse", "--show-toplevel"],
                capture_output=True,
                check=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            raise SourcePortError(f"{root} is not inside a git work tree") from e
        self._repo_root = Path(proc.stdout.decode("utf-8", "replace").strip()).resolve()

    def canonicalize_resource(self, uri: str) -> str:
        return f"git:{self._relpath(uri)}"

    def canonical_destination_id(self, target: str) -> str:
        # Same canonical form: the repo-relative path is the authoritative
        # destination identity the auditor logs.
        return f"git:{self._relpath(target)}"

    def _relpath(self, uri: str) -> str:
        raw = uri
        for prefix in ("git:", "file://"):
            if raw.startswith(prefix):
                raw = raw[len(prefix) :]
        if not raw.strip():
            raise SourcePortError(f"empty resource uri {uri!r}")
        candidate = (self._repo_root / raw).resolve()
        try:
            rel = candidate.relative_to(self._repo_root)
        except ValueError as e:
            raise SourcePortError(
                f"resource {uri!r} escapes the repository root",
            ) from e
        if not rel.parts:
            raise SourcePortError(f"resource {uri!r} resolves to the repo root")
        return rel.as_posix()
