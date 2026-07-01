"""Daemon RPC handlers for typed review artifacts."""

from __future__ import annotations

import hashlib
from typing import Any

from capabledeputy.artifacts import TypedArtifact, artifact_approval_payload, artifact_review_card
from capabledeputy.daemon.handlers import Handler


def make_artifact_handlers() -> dict[str, Handler]:
    async def prepare(params: dict[str, Any]) -> dict[str, Any]:
        payload = params.get("artifact") or params
        if not isinstance(payload, dict):
            raise ValueError("artifact.prepare payload must be a mapping")
        artifact = TypedArtifact.from_dict(payload)
        return {"artifact": artifact.to_dict(), "review_artifact": artifact_review_card(artifact)}

    async def approval_payload(params: dict[str, Any]) -> dict[str, Any]:
        payload = params.get("artifact") or {}
        if not isinstance(payload, dict):
            raise ValueError("artifact.approval_payload artifact must be a mapping")
        artifact = TypedArtifact.from_dict(payload)
        labels = params.get("labels_in") or []
        if not isinstance(labels, list | tuple | set | frozenset):
            raise ValueError("artifact.approval_payload labels_in must be a list or set")
        message = artifact_approval_payload(
            approval_id=int(params.get("approval_id") or 0),
            action=str(params.get("action") or ""),
            artifact=artifact,
            labels_in=frozenset(str(label) for label in labels),
        )
        return {
            "canonical_payload": message.decode("utf-8"),
            "canonical_payload_sha256": hashlib.sha256(message).hexdigest(),
            "artifact_sha256": artifact.sha256,
            "destination_id": artifact.destination_id,
        }

    return {
        "artifact.prepare": prepare,
        "artifact.approval_payload": approval_payload,
    }
