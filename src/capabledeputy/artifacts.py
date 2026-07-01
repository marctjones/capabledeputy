"""Typed review artifacts for foreground approvals.

Artifacts are the bytes the operator reviewed: a draft, diff, calendar
proposal, document patch, or research brief. The artifact hash is a stable
binding used by signed approvals so "approved" means this exact artifact and
destination, not a later reconstruction.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from capabledeputy.approval.signer import canonical_payload
from capabledeputy.policy.labels import LabelState


class ArtifactError(ValueError):
    """Invalid artifact payload."""


class ArtifactType(StrEnum):
    EMAIL_DRAFT = "email_draft"
    DIFF = "diff"
    CALENDAR_EVENT = "calendar_event"
    DOCUMENT = "document"
    RESEARCH = "research"
    IMAGE = "image"
    CHART = "chart"


class ArtifactEffect(StrEnum):
    REVIEW_ONLY = "review_only"
    CREATE = "create"
    MODIFY = "modify"
    SEND = "send"
    DELETE = "delete"


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class TypedArtifact:
    artifact_type: ArtifactType
    title: str
    content: str
    target: str
    destination_id: str
    effect: ArtifactEffect
    content_type: str = "text/plain"
    labels: LabelState = field(default_factory=LabelState)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utcnow)
    artifact_id: str = ""

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ArtifactError("artifact title is required")
        if not self.destination_id.strip():
            raise ArtifactError("artifact destination_id is required")
        if not self.content_type.strip():
            raise ArtifactError("artifact content_type is required")
        if not isinstance(self.labels, LabelState):
            raise ArtifactError("artifact labels must be a LabelState")

    @property
    def sha256(self) -> str:
        return artifact_hash(self)

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "artifact_type": self.artifact_type.value,
            "title": self.title,
            "content": self.content,
            "content_type": self.content_type,
            "target": self.target,
            "destination_id": self.destination_id,
            "effect": self.effect.value,
            "labels": self.labels.to_dict(),
            "metadata": _jsonable(self.metadata),
        }

    def to_dict(self) -> dict[str, Any]:
        artifact_id = self.artifact_id or self.sha256[:16]
        return {
            **self.canonical_dict(),
            "artifact_id": artifact_id,
            "sha256": self.sha256,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TypedArtifact:
        raw_type = str(data.get("artifact_type") or data.get("type") or "")
        raw_effect = str(data.get("effect") or data.get("effect_class") or "")
        try:
            artifact_type = ArtifactType(raw_type)
        except ValueError as e:
            raise ArtifactError(f"unknown artifact_type: {raw_type!r}") from e
        try:
            effect = ArtifactEffect(raw_effect or ArtifactEffect.REVIEW_ONLY.value)
        except ValueError as e:
            raise ArtifactError(f"unknown artifact effect: {raw_effect!r}") from e
        labels = LabelState.from_dict(data.get("labels"))
        created_raw = str(data.get("created_at") or "")
        created = datetime.fromisoformat(created_raw) if created_raw else _utcnow()
        metadata = data.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ArtifactError("artifact metadata must be a mapping")
        return cls(
            artifact_type=artifact_type,
            title=str(data.get("title") or ""),
            content=str(data.get("content") or ""),
            target=str(data.get("target") or ""),
            destination_id=str(data.get("destination_id") or data.get("target") or ""),
            effect=effect,
            content_type=str(data.get("content_type") or "text/plain"),
            labels=labels,
            metadata={str(k): v for k, v in metadata.items()},
            created_at=created,
            artifact_id=str(data.get("artifact_id") or ""),
        )


def artifact_hash(artifact: TypedArtifact) -> str:
    body = json.dumps(
        artifact.canonical_dict(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def artifact_approval_payload(
    *,
    approval_id: int,
    action: str,
    artifact: TypedArtifact,
    labels_in: frozenset[str] | list[str],
) -> bytes:
    """Build canonical approval bytes bound to an exact artifact hash."""

    return canonical_payload(
        approval_id=approval_id,
        action=action,
        target=artifact.destination_id,
        payload=artifact.content,
        labels_in=labels_in,
        artifact_hash=artifact.sha256,
        destination_id=artifact.destination_id,
    )


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value, sort_keys=True)
    except TypeError:
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return value
