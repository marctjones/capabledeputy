"""#12 (remaining) — git-backed SourcePort + VersionedWritePort providers.

Proves the first concrete adapters behind the substrate ports:
  - GitVersionedWritePort writes commit-versioned content and surfaces a
    `prior_version_handle` that `read_prior_version_hash` resolves to the
    exact pre-write hash, so `verify_write_discipline` earns
    `reversible/system` (FR-044).
  - GitSourcePort canonicalizes targets to stable repo-relative ids and
    fail-closes on path escapes (FR-048).
  - The registries (`get_*_port`) construct providers + fail-closed on
    unknown kinds (the modular plug-in point for future backends).
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from capabledeputy.policy.reversibility import (
    ReversalAgent,
    ReversibilityDegree,
    verify_write_discipline,
)
from capabledeputy.substrate.git_source import GitSourcePort, SourcePortError
from capabledeputy.substrate.git_versioned_write import (
    GitVersionedWritePort,
    VersionedWritePortError,
)
from capabledeputy.substrate.source_port import get_source_port
from capabledeputy.substrate.version_write_port import get_versioned_write_port


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def test_write_new_file_commits_and_reports_no_prior(repo: Path) -> None:
    port = GitVersionedWritePort(repo)
    content = b"first version\n"
    result = port.write(target="notes/a.txt", content=content)

    assert result.prior_version_handle is None  # brand-new file
    assert result.post_state_hash == _sha(content)
    assert result.attestation.startswith("git-history:")
    assert (repo / "notes" / "a.txt").read_bytes() == content
    # committed (clean tree)
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True,
        check=True,
    )
    assert status.stdout == b""


def test_overwrite_surfaces_verifiable_prior_version(repo: Path) -> None:
    port = GitVersionedWritePort(repo)
    v1 = b"version one\n"
    v2 = b"version two\n"
    port.write(target="notes/a.txt", content=v1)
    result = port.write(target="notes/a.txt", content=v2)

    assert result.prior_version_handle is not None
    observed = port.read_prior_version_hash(result.prior_version_handle)
    assert observed == _sha(v1)  # the exact pre-write content hash

    label = verify_write_discipline(
        result,
        observed_prior_hash=observed,
        expected_pre_state_hash=_sha(v1),
    )
    assert label.degree is ReversibilityDegree.REVERSIBLE
    assert label.agent is ReversalAgent.SYSTEM


def test_read_prior_version_hash_unknown_handle_is_none(repo: Path) -> None:
    port = GitVersionedWritePort(repo)
    assert port.read_prior_version_hash("deadbeef:nope.txt") is None


def test_write_target_escape_is_rejected(repo: Path) -> None:
    port = GitVersionedWritePort(repo)
    with pytest.raises(VersionedWritePortError, match="escapes"):
        port.write(target="../outside.txt", content=b"x")


def test_noop_rewrite_stays_verifiable(repo: Path) -> None:
    port = GitVersionedWritePort(repo)
    content = b"same\n"
    port.write(target="a.txt", content=content)
    result = port.write(target="a.txt", content=content)  # identical content
    observed = port.read_prior_version_hash(result.prior_version_handle or "")
    assert observed == _sha(content)


def test_source_port_canonicalizes_and_strips_scheme(repo: Path) -> None:
    sp = GitSourcePort(repo)
    assert sp.canonicalize_resource("notes/a.txt") == "git:notes/a.txt"
    assert sp.canonicalize_resource("git:notes/a.txt") == "git:notes/a.txt"
    assert sp.canonical_destination_id(str(repo / "notes" / "a.txt")) == "git:notes/a.txt"
    assert sp.surfaces_destination_id is True


def test_source_port_escape_is_rejected(repo: Path) -> None:
    sp = GitSourcePort(repo)
    with pytest.raises(SourcePortError, match="escapes"):
        sp.canonicalize_resource("../etc/passwd")


def test_non_git_dir_fails_closed(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(VersionedWritePortError):
        GitVersionedWritePort(plain)
    with pytest.raises(SourcePortError):
        GitSourcePort(plain)


def test_registries_construct_and_fail_closed(repo: Path) -> None:
    assert isinstance(get_versioned_write_port("git", repo_root=repo), GitVersionedWritePort)
    assert isinstance(get_source_port("git", repo_root=repo), GitSourcePort)
    with pytest.raises(ValueError, match="unknown versioned-write provider"):
        get_versioned_write_port("s3", repo_root=repo)
    with pytest.raises(ValueError, match="unknown source-port provider"):
        get_source_port("gmail", repo_root=repo)
