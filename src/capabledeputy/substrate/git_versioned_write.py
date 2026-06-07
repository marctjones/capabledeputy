"""Git-backed `VersionedWritePort` provider (003 US6 T075 / FR-044).

The first concrete provider behind `VersionedWritePort`. A write commits
the new content to a git repository, so the pre-write state is retained
in history and is content-addressed (tamper-evident). The
`prior_version_handle` is git's own ``<commit>:<path>`` revspec, which
``read_prior_version_hash`` resolves with ``git show`` — so the policy
verifier (``verify_write_discipline``) can confirm the prior version is
retrievable and the write earns ``reversible/system``.

Modular by design: this is one adapter behind the port. Other backends
(S3 object-lock, SharePoint version history, …) implement the same
`VersionedWritePort` ABC and register via `get_versioned_write_port`.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

from capabledeputy.policy.reversibility import WriteResult


class VersionedWritePortError(RuntimeError):
    """Fail-closed error from a versioned-write provider (e.g. target
    escapes the repo, or git is unavailable). Typed so callers convert
    cleanly to IRREVERSIBLE/EXTERNAL per FR-044."""


class GitVersionedWritePort:
    """`VersionedWritePort` backed by a local git repository.

    `repo_root` must be (inside) a git work tree. Every `write` stages +
    commits the target path; the prior committed version stays reachable
    in history for the retention window (i.e. until history is rewritten).
    """

    def __init__(
        self,
        repo_root: Path | str,
        *,
        git_bin: str | None = None,
        author_name: str = "capabledeputy",
        author_email: str = "capdep@localhost",
    ) -> None:
        self._git_bin = git_bin or shutil.which("git") or "git"
        self._author_name = author_name
        self._author_email = author_email
        root = Path(repo_root).resolve()
        # Resolve to the actual work-tree root; fail-closed if not a repo.
        try:
            top = self._git("rev-parse", "--show-toplevel", cwd=root).strip()
        except VersionedWritePortError as e:
            raise VersionedWritePortError(
                f"{root} is not inside a git work tree: {e}",
            ) from e
        self._repo_root = Path(top).resolve()

    # --- VersionedWritePort interface ---------------------------------

    def write(self, *, target: str, content: bytes) -> WriteResult:
        relpath = self._relpath(target)
        prior_handle: str | None = None
        head = self._head_commit()
        if head is not None and self._exists_in(head, relpath):
            prior_handle = f"{head}:{relpath}"

        abspath = self._repo_root / relpath
        abspath.parent.mkdir(parents=True, exist_ok=True)
        abspath.write_bytes(content)
        self._git("add", "--", relpath)

        # A no-op write (content identical to HEAD) leaves nothing staged;
        # skip the commit but still return a valid, verifiable result.
        staged = self._git("status", "--porcelain", "--", relpath).strip()
        if staged:
            self._git(
                "-c",
                f"user.name={self._author_name}",
                "-c",
                f"user.email={self._author_email}",
                "commit",
                "-q",
                "-m",
                f"versioned-write: {relpath}",
                "--",
                relpath,
            )
        new_commit = self._head_commit()
        return WriteResult(
            prior_version_handle=prior_handle,
            post_state_hash=hashlib.sha256(content).hexdigest(),
            # Git commit SHAs are content-addressed: the attestation is the
            # commit that now anchors history (and thus the retained prior
            # version). Tamper-evident without a separate signing key.
            attestation=f"git-history:{new_commit}",
        )

    def read_prior_version_hash(self, prior_version_handle: str) -> str | None:
        # Handle is a git "<commit>:<path>" revspec.
        try:
            content = self._git_bytes("show", prior_version_handle)
        except VersionedWritePortError:
            return None
        return hashlib.sha256(content).hexdigest()

    # --- helpers ------------------------------------------------------

    def _relpath(self, target: str) -> str:
        """Canonical repo-relative POSIX path. Fail-closed on any path
        that escapes the repo (`..`, absolute outside-root)."""
        raw = target
        for prefix in ("git:", "file://"):
            if raw.startswith(prefix):
                raw = raw[len(prefix) :]
        candidate = (self._repo_root / raw).resolve()
        try:
            rel = candidate.relative_to(self._repo_root)
        except ValueError as e:
            raise VersionedWritePortError(
                f"write target {target!r} escapes the repository root",
            ) from e
        if not rel.parts:
            raise VersionedWritePortError(f"write target {target!r} is the repo root")
        return rel.as_posix()

    def _head_commit(self) -> str | None:
        try:
            return self._git("rev-parse", "-q", "--verify", "HEAD").strip()
        except VersionedWritePortError:
            return None  # no commits yet

    def _exists_in(self, commit: str, relpath: str) -> bool:
        try:
            self._git("cat-file", "-e", f"{commit}:{relpath}")
        except VersionedWritePortError:
            return False
        return True

    def _git(self, *args: str, cwd: Path | None = None) -> str:
        proc = self._run(args, cwd=cwd)
        return proc.stdout.decode("utf-8", "replace")

    def _git_bytes(self, *args: str) -> bytes:
        return self._run(args).stdout

    def _run(
        self,
        args: tuple[str, ...],
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(
                [self._git_bin, "-C", str(cwd or self._repo_root), *args],
                capture_output=True,
                check=True,
            )
        except FileNotFoundError as e:
            raise VersionedWritePortError(f"git binary {self._git_bin!r} not found") from e
        except subprocess.CalledProcessError as e:
            raise VersionedWritePortError(
                f"git {' '.join(args)} failed: {e.stderr.decode('utf-8', 'replace').strip()}",
            ) from e
