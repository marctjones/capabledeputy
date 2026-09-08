"""Cloud VersionedWritePort provider tests for v0.37."""

from __future__ import annotations

import hashlib
from typing import Any

from capabledeputy.substrate.cloud_versioned_write import S3ObjectLockVersionedWritePort
from capabledeputy.substrate.version_write_port import get_versioned_write_port


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class _FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, list[tuple[str, bytes]]] = {}

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        key = kwargs["Key"]
        version_id_arg = kwargs.get("VersionId")
        versions = self.objects.get(key)
        if not versions:
            raise KeyError(key)
        if version_id_arg is None:
            version_id, content = versions[-1]
        else:
            version_id, content = next(v for v in versions if v[0] == version_id_arg)
        return {"VersionId": version_id, "Body": content}

    def put_object(self, **kwargs: Any) -> dict[str, str]:
        key = kwargs["Key"]
        body = kwargs["Body"]
        versions = self.objects.setdefault(key, [])
        version_id = f"v{len(versions) + 1}"
        versions.append((version_id, body))
        return {"VersionId": version_id}


def test_s3_object_lock_write_surfaces_prior_version() -> None:
    fake = _FakeS3()
    port = S3ObjectLockVersionedWritePort(
        bucket="capdep-test",
        prefix="writes",
        s3_client=fake,
    )
    first = port.write(target="notes/a.txt", content=b"one")
    second = port.write(target="notes/a.txt", content=b"two")
    assert first.prior_version_handle is None
    assert second.prior_version_handle is not None
    assert second.attestation.startswith("s3-object-lock:capdep-test:writes/notes/a.txt")
    assert second.post_state_hash == _sha(b"two")
    assert port.read_prior_version_hash(second.prior_version_handle) == _sha(b"one")


def test_versioned_write_registry_knows_cloud_backends() -> None:
    fake = _FakeS3()
    s3 = get_versioned_write_port(
        "s3-object-lock",
        bucket="capdep-test",
        s3_client=fake,
    )
    assert isinstance(s3, S3ObjectLockVersionedWritePort)
