"""Audit-backed materialized provenance DAG.

The graph is intentionally bounded to CapDep materialization boundaries:
capabilities, reference-handle binds, tool results, declassifier outputs,
approval decisions, and delegation edges. It does not attempt to explain
model-internal token generation.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from capabledeputy.audit.events import Event, EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.policy.labels import LabelState


def _node_id(kind: str, identifier: object) -> str:
    return f"{kind}:{identifier}"


def capability_node_id(audit_id: UUID | str) -> str:
    return _node_id("capability", audit_id)


def reference_handle_node_id(handle_id: UUID | str) -> str:
    return _node_id("reference_handle", handle_id)


def reference_bind_node_id(audit_id: UUID | str) -> str:
    return _node_id("reference_bind", audit_id)


def tool_result_node_id(audit_id: UUID | str) -> str:
    return _node_id("tool_result", audit_id)


def declassifier_output_node_id(audit_id: UUID | str) -> str:
    return _node_id("declassifier_output", audit_id)


def approval_request_node_id(approval_id: int | str) -> str:
    return _node_id("approval_request", approval_id)


def approval_decision_node_id(audit_id: UUID | str) -> str:
    return _node_id("approval_decision", audit_id)


def stable_digest(value: Any) -> str:
    """Return a stable digest for arbitrary JSON-ish materialized output."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _labels_payload(labels: LabelState | None) -> dict[str, Any] | None:
    return labels.to_dict() if labels is not None and labels != LabelState() else None


class ProvenanceRecorder:
    """Emit provenance DAG nodes and edges into the tamper-evident audit log."""

    def __init__(self, audit: AuditWriter | None) -> None:
        self._audit = audit

    async def node(
        self,
        *,
        session_id: UUID | None,
        node_id: str,
        kind: str,
        materialized_id: str | None = None,
        label_state: LabelState | None = None,
        event_audit_id: UUID | str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self._audit is None:
            return
        payload: dict[str, Any] = {
            "node_id": node_id,
            "kind": kind,
        }
        if materialized_id is not None:
            payload["materialized_id"] = materialized_id
        labels = _labels_payload(label_state)
        if labels is not None:
            payload["label_state"] = labels
        if event_audit_id is not None:
            payload["event_audit_id"] = str(event_audit_id)
        if metadata:
            payload["metadata"] = metadata
        await self._audit.write(
            Event(
                event_type=EventType.PROVENANCE_NODE,
                session_id=session_id,
                payload=payload,
            ),
        )

    async def edge(
        self,
        *,
        session_id: UUID | None,
        from_node_id: str,
        to_node_id: str,
        kind: str,
        event_audit_id: UUID | str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self._audit is None:
            return
        payload: dict[str, Any] = {
            "from_node_id": from_node_id,
            "to_node_id": to_node_id,
            "kind": kind,
        }
        if event_audit_id is not None:
            payload["event_audit_id"] = str(event_audit_id)
        if metadata:
            payload["metadata"] = metadata
        await self._audit.write(
            Event(
                event_type=EventType.PROVENANCE_EDGE,
                session_id=session_id,
                payload=payload,
            ),
        )
