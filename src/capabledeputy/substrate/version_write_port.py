"""Versioned-write port — interface only (003 US6 T075 / FR-044).

A `VersionedWritePort.write` MUST return a `WriteResult` carrying
the `prior_version_handle`, the `post_state_hash`, and an
`attestation` confirming retention. The policy layer uses these
to *verify* (T083, FR-044) whether the write earns
`reversible/system` or stays `irreversible`.

Provider impls (S3 with object lock, SharePoint with version
history, etc.) live in spec 004.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from capabledeputy.policy.reversibility import WriteResult


class VersionedWritePort(ABC):
    """Port interface. Implementations MUST guarantee that
    `prior_version_handle` (when non-None) can be read for the
    declared retention window; the attestation is a signed
    confirmation of that promise (FR-044)."""

    @abstractmethod
    def write(self, *, target: str, content: bytes) -> WriteResult:
        """Perform a versioned write. Return the WriteResult the
        policy verifier consumes. May raise on transient failure;
        callers convert into IRREVERSIBLE/EXTERNAL per FR-044."""

    @abstractmethod
    def read_prior_version_hash(self, prior_version_handle: str) -> str | None:
        """Read the pre-write state's hash for verification. Return
        None if the handle has expired or never existed — the policy
        verifier converts that into IRREVERSIBLE/EXTERNAL."""


# Provider registry. New backends (s3-object-lock, sharepoint, …) add a
# branch here + a module implementing `VersionedWritePort`; callers select
# by `kind` (e.g. from daemon.yaml). Lazy import keeps this port module
# free of its concrete providers.
def get_versioned_write_port(kind: str, **kwargs: object) -> VersionedWritePort:
    """Construct a VersionedWritePort provider. Fail-closed on unknown
    kind (Constitution VI)."""
    if kind == "git":
        from capabledeputy.substrate.git_versioned_write import GitVersionedWritePort

        return GitVersionedWritePort(**kwargs)  # type: ignore[arg-type]
    if kind in {"s3", "s3-object-lock", "s3_object_lock"}:
        from capabledeputy.substrate.cloud_versioned_write import (
            S3ObjectLockVersionedWritePort,
        )

        return S3ObjectLockVersionedWritePort(**kwargs)  # type: ignore[arg-type]
    if kind in {"google-drive", "google-drive-revisions", "gdrive-revisions"}:
        from capabledeputy.substrate.cloud_versioned_write import (
            GoogleDriveRevisionVersionedWritePort,
        )

        return GoogleDriveRevisionVersionedWritePort(**kwargs)  # type: ignore[arg-type]
    known = ["git", "s3-object-lock", "google-drive-revisions"]
    raise ValueError(f"unknown versioned-write provider {kind!r}; known: {known}")
