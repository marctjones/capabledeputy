from __future__ import annotations

import json

import pytest

from capabledeputy.approval.signer import SoftwareKeySigner
from capabledeputy.artifacts import (
    ArtifactEffect,
    ArtifactError,
    ArtifactType,
    TypedArtifact,
    artifact_approval_payload,
)
from capabledeputy.daemon.artifact_handlers import make_artifact_handlers


def _artifact(content: str = "Hello") -> TypedArtifact:
    return TypedArtifact(
        artifact_type=ArtifactType.EMAIL_DRAFT,
        title="Draft reply",
        content=content,
        content_type="text/markdown",
        target="mailto:alice@example.com",
        destination_id="gmail:recipient:alice@example.com",
        effect=ArtifactEffect.SEND,
        metadata={"thread": "thread-1"},
    )


def test_typed_artifact_hash_changes_with_reviewed_bytes() -> None:
    first = _artifact("Hello")
    second = _artifact("Hello!")

    assert first.sha256 != second.sha256
    assert first.to_dict()["artifact_id"] == first.sha256[:16]


def test_artifact_requires_destination_id() -> None:
    with pytest.raises(ArtifactError):
        TypedArtifact(
            artifact_type=ArtifactType.DIFF,
            title="Patch",
            content="diff --git a",
            target="",
            destination_id="",
            effect=ArtifactEffect.MODIFY,
        )


def test_artifact_approval_payload_binds_hash_and_destination() -> None:
    artifact = _artifact()
    message = artifact_approval_payload(
        approval_id=7,
        action="SEND_EMAIL",
        artifact=artifact,
        labels_in=frozenset({"trusted.user_direct"}),
    )
    body = json.loads(message)

    assert body["artifact_hash"] == artifact.sha256
    assert body["destination_id"] == "gmail:recipient:alice@example.com"
    assert body["target"] == "gmail:recipient:alice@example.com"

    signer = SoftwareKeySigner(key=b"x" * 32, key_id="sw:test")
    signature = signer.sign(message)
    tampered = artifact_approval_payload(
        approval_id=7,
        action="SEND_EMAIL",
        artifact=_artifact("Changed"),
        labels_in=frozenset({"trusted.user_direct"}),
    )
    assert signer.verify(message, signature) is True
    assert signer.verify(tampered, signature) is False


async def test_artifact_handlers_prepare_and_canonical_payload() -> None:
    handlers = make_artifact_handlers()
    prepared = await handlers["artifact.prepare"]({"artifact": _artifact().to_dict()})
    payload = await handlers["artifact.approval_payload"](
        {
            "approval_id": 7,
            "action": "SEND_EMAIL",
            "artifact": prepared["artifact"],
            "labels_in": ["trusted.user_direct"],
        },
    )

    assert prepared["artifact"]["sha256"] == payload["artifact_sha256"]
    assert payload["destination_id"] == "gmail:recipient:alice@example.com"
    assert json.loads(payload["canonical_payload"])["artifact_hash"] == payload["artifact_sha256"]
