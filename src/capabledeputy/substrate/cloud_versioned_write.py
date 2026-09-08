"""Cloud-backed VersionedWritePort providers.

These providers sit behind the same ``VersionedWritePort`` contract as
the git implementation. They are deliberately dependency-light:
operators may pass real SDK clients, while tests pass tiny fakes. When
no client is supplied the constructor attempts a lazy import and fails
closed with a clear error.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from capabledeputy.policy.reversibility import WriteResult
from capabledeputy.substrate.git_versioned_write import VersionedWritePortError

_SAFE_TARGET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@+=:-]{0,1023}$")


def _hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _safe_target(target: str) -> str:
    raw = target.strip().lstrip("/")
    if not raw or ".." in raw.split("/") or not _SAFE_TARGET.match(raw):
        raise VersionedWritePortError(f"write target {target!r} is not a stable safe id")
    return raw


def _body_bytes(body: Any) -> bytes:
    if body is None:
        return b""
    if isinstance(body, bytes):
        return body
    if isinstance(body, str):
        return body.encode("utf-8")
    if hasattr(body, "read"):
        return body.read()
    return bytes(body)


@dataclass(frozen=True)
class S3ObjectLockVersionedWritePort:
    """S3 provider using bucket versioning + Object Lock retention."""

    bucket: str
    prefix: str = ""
    retention_days: int = 30
    s3_client: Any | None = None

    def __post_init__(self) -> None:
        if not self.bucket:
            raise VersionedWritePortError("s3-object-lock bucket is required")
        if self.retention_days < 1:
            raise VersionedWritePortError("s3-object-lock retention_days must be positive")
        if self.s3_client is None:
            try:
                import boto3  # type: ignore[import-not-found]
            except ImportError as e:  # pragma: no cover - optional dependency
                raise VersionedWritePortError(
                    "boto3 is required unless s3_client is injected",
                ) from e
            object.__setattr__(self, "s3_client", boto3.client("s3"))

    def write(self, *, target: str, content: bytes) -> WriteResult:
        key = self._key(target)
        prior_handle = self._prior_handle(key)
        retain_until = datetime.now(UTC) + timedelta(days=self.retention_days)
        assert self.s3_client is not None
        response = self.s3_client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=content,
            ObjectLockMode="GOVERNANCE",
            ObjectLockRetainUntilDate=retain_until,
        )
        version_id = str(response.get("VersionId") or "")
        return WriteResult(
            prior_version_handle=prior_handle,
            post_state_hash=_hash_bytes(content),
            attestation=(
                f"s3-object-lock:{self.bucket}:{key}:version={version_id}:"
                f"retain-until={retain_until.isoformat()}"
            ),
        )

    def read_prior_version_hash(self, prior_version_handle: str) -> str | None:
        parsed = urlparse(prior_version_handle)
        if parsed.scheme != "s3-object-lock":
            return None
        bucket = parsed.netloc
        key = unquote(parsed.path.lstrip("/"))
        version_id = (parse_qs(parsed.query).get("versionId") or [""])[0]
        if not bucket or not key or not version_id:
            return None
        assert self.s3_client is not None
        try:
            response = self.s3_client.get_object(
                Bucket=bucket,
                Key=key,
                VersionId=version_id,
            )
        except Exception:
            return None
        return _hash_bytes(_body_bytes(response.get("Body")))

    def _key(self, target: str) -> str:
        rel = _safe_target(target)
        prefix = self.prefix.strip("/")
        return f"{prefix}/{rel}" if prefix else rel

    def _prior_handle(self, key: str) -> str | None:
        assert self.s3_client is not None
        try:
            response = self.s3_client.get_object(Bucket=self.bucket, Key=key)
        except Exception:
            return None
        version_id = response.get("VersionId")
        if not version_id:
            return None
        return f"s3-object-lock://{self.bucket}/{quote(key)}?versionId={quote(str(version_id))}"
