"""Cloud VersionedWritePort provider tests for v0.37."""

from __future__ import annotations

import hashlib
from typing import Any

from capabledeputy.substrate.cloud_versioned_write import (
    GoogleDriveRevisionVersionedWritePort,
    S3ObjectLockVersionedWritePort,
)
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


class _FakeExecute:
    def __init__(self, value: Any) -> None:
        self.value = value

    def execute(self) -> Any:
        return self.value


class _FakeDriveService:
    def __init__(self) -> None:
        self.revisions_by_file = {"file123": [("r1", b"old")]}
        self.keep_forever_updates: list[tuple[str, str]] = []

    def files(self) -> _FakeDriveService:
        return self

    def revisions(self) -> _FakeDriveService:
        return self

    def update(self, **kwargs: Any) -> _FakeExecute:
        file_id = kwargs["fileId"]
        media_body = kwargs.get("media_body")
        if "revisionId" in kwargs:
            self.keep_forever_updates.append((file_id, kwargs["revisionId"]))
            return _FakeExecute({"id": kwargs["revisionId"], "keepForever": True})
        if hasattr(media_body, "_fd"):
            media_body._fd.seek(0)  # type: ignore[attr-defined]
            content = media_body._fd.read()  # type: ignore[attr-defined]
        else:
            content = bytes(media_body)  # pyright: ignore[reportArgumentType]
        revisions = self.revisions_by_file.setdefault(file_id, [])
        revision_id = f"r{len(revisions) + 1}"
        revisions.append((revision_id, content))
        return _FakeExecute(
            {
                "id": file_id,
                "version": str(len(revisions)),
                "headRevisionId": revision_id,
            },
        )

    def list(self, **kwargs: Any) -> _FakeExecute:
        revisions = self.revisions_by_file.get(kwargs["fileId"], [])
        return _FakeExecute({"revisions": [{"id": rev_id} for rev_id, _content in revisions]})

    def get(self, **kwargs: Any) -> _FakeExecute:
        revisions = self.revisions_by_file.get(kwargs["fileId"], [])
        content = next(content for rev_id, content in revisions if rev_id == kwargs["revisionId"])
        return _FakeExecute(content)


def test_google_drive_revision_write_surfaces_prior_revision() -> None:
    fake = _FakeDriveService()
    port = GoogleDriveRevisionVersionedWritePort(drive_service=fake)
    result = port.write(target="google-drive:file:file123", content=b"new")
    assert result.prior_version_handle == "google-drive-revisions://file123?revisionId=r1"
    assert result.post_state_hash == _sha(b"new")
    assert result.attestation.startswith("google-drive-revisions:file=file123")
    assert fake.keep_forever_updates == [("file123", "r2")]
    assert port.read_prior_version_hash(result.prior_version_handle) == _sha(b"old")


def test_versioned_write_registry_knows_cloud_backends() -> None:
    fake = _FakeS3()
    s3 = get_versioned_write_port(
        "s3-object-lock",
        bucket="capdep-test",
        s3_client=fake,
    )
    assert isinstance(s3, S3ObjectLockVersionedWritePort)
    drive = get_versioned_write_port(
        "google-drive-revisions",
        drive_service=_FakeDriveService(),
    )
    assert isinstance(drive, GoogleDriveRevisionVersionedWritePort)
