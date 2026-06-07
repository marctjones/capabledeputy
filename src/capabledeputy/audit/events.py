"""Audit event taxonomy.

Defines the canonical wire format for the audit log (DESIGN.md §9.2).
Every event in the log is one of these types, serialized to JSONL.
The full taxonomy is wired in from Phase 1 even though most emitters
arrive in later phases — retrofitting the trace shape is painful.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self
from uuid import UUID, uuid4


class EventType(StrEnum):
    SESSION_CREATED = "session.created"
    SESSION_FORKED = "session.forked"
    SESSION_PAUSED = "session.paused"
    SESSION_RESUMED = "session.resumed"
    SESSION_MERGED = "session.merged"
    SESSION_ABORTED = "session.aborted"
    SESSION_DONE = "session.done"

    LLM_CONTEXT_ASSEMBLED = "llm.context_assembled"
    LLM_REQUEST_SENT = "llm.request_sent"
    LLM_RESPONSE_RECEIVED = "llm.response_received"
    LLM_RESPONSE_PARSED = "llm.response_parsed"
    # Issue #36 — LLM call failed with an exception (context overflow,
    # rate limit, timeout, network, etc.). Payload carries error_type,
    # message, iteration, approximate context_size. Closes the audit
    # gap where exceptions silently propagated up without trace.
    LLM_ERROR = "llm.error"
    # Issue #36 — context size approaching the model's window. Soft
    # signal emitted when estimate exceeds 80% of the window so the
    # operator/agent can see proactively that the turn is about to
    # outgrow its budget.
    LLM_CONTEXT_WARNING = "llm.context_warning"
    # Issue #2 — the agent loop terminated abnormally. AGENT_LOOP_EXCEEDED
    # fires when the iteration cap (`max_iterations`) is hit;
    # AGENT_LOOP_THRASHING fires earlier when the same (tool, args) is
    # proposed repeatedly within one turn. Both carry the last-N tool
    # calls (name + args) so the pathological turn is inspectable /
    # replayable from the audit log instead of vanishing into an opaque
    # RPC error. `capdep audit ... --filter loop` surfaces them.
    AGENT_LOOP_EXCEEDED = "agent.loop_exceeded"
    AGENT_LOOP_THRASHING = "agent.loop_thrashing"
    # Issue 003 / Q4 (spec.md §Clarifications 2026-05-25, SC-023):
    # decide() latency exceeded the p95 ≤ 50 ms OR p99.9 ≤ 250 ms
    # target over the recent window. Payload carries:
    #   latency_ms, threshold_crossed: "p95"|"p99.9",
    #   window_size, observed_p95_ms, observed_p99_9_ms, rule_count.
    # Operator-visible signal so latency regressions are caught
    # before they manifest as subjective REPL sluggishness.
    DECISION_LATENCY_DEGRADED = "decision.latency_degraded"

    MODE_SELECTED = "mode.selected"
    POLICY_DECIDED = "policy.decided"
    # Cookbook Pattern ⑥ — session in SHADOW enforcement mode and the
    # engine returned a non-ALLOW outcome. The dispatcher rewrites to
    # ALLOW and emits this event with the original decision so audit
    # replay can answer "what would have happened under STRICT?"
    POLICY_SHADOWED = "policy.shadowed"
    # The operator (or a programmatic caller) flipped a session's
    # enforcement_mode. Payload carries old + new mode so the audit
    # log distinguishes shadow-time from strict-time decisions for
    # the same session.
    ENFORCEMENT_MODE_CHANGED = "enforcement.mode_changed"
    # Spec 004 P0 — programmatic primitive applications.
    # INSPECTOR_APPLIED: a RaiseOnlyInspector raised session axes
    # DECISION_INSPECTOR_APPLIED: a DecisionInspector relaxed/tightened
    # DECLASSIFIER_APPLIED: a DeclassifyingTransformer lowered + transformed
    INSPECTOR_APPLIED = "inspector.applied"
    DECISION_INSPECTOR_APPLIED = "decision_inspector.applied"
    DECLASSIFIER_APPLIED = "declassifier.applied"

    LABEL_PROPAGATED = "label.propagated"
    CAPABILITY_CHECKED = "capability.checked"
    CAPABILITY_GRANTED = "capability.granted"

    # 002 capability delegation chains.
    DELEGATION_GRANTED = "delegation.granted"
    DELEGATION_REFUSED = "delegation.refused"
    CAPABILITY_CASCADE_REVOKED = "capability.cascade_revoked"

    # 003 v0.9 labeling framework — T014.
    BINDING_APPLIED = "binding.applied"  # FR-043 Source/Location Binding
    OVERRIDE_GRANTED = "override.granted"  # FR-032
    OVERRIDE_ATTESTED = "override.attested"  # FR-036 dual-control
    OVERRIDE_REFUSED = "override.refused"  # FR-036
    OVERRIDE_EXPIRED = "override.expired"  # FR-036
    OVERRIDE_USE_REFUSED = "override.use_refused"  # FR-038
    RATIFICATION_APPLIED = "ratification.applied"  # FR-014 Q3
    PATTERN3_HANDLE_BIND = "pattern3.handle_bind"  # FR-047 Reference Handle
    ISOLATION_REGION_CREATED = "isolation_region.created"  # FR-040
    ISOLATION_REGION_DISCARDED = "isolation_region.discarded"  # FR-040
    ENVELOPE_DIAL_CHANGED = "envelope.dial_changed"  # FR-030 owner-set
    RISK_REGISTER_AUDIT = "risk_register.audit"  # FR-015/028 orphan audit
    RESIDUAL_RISK_EXCEPTION = "residual_risk.exception"  # FR-016 threshold-crossing
    RELAXATION_REFUSED = "policy.relaxation_refused"  # FR-031 (T046)

    TOOL_DISPATCHED = "tool.dispatched"
    TOOL_RETURNED = "tool.returned"

    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_APPROVED = "approval.approved"
    APPROVAL_DENIED = "approval.denied"
    APPROVAL_DEFERRED = "approval.deferred"
    APPROVAL_EXPIRED = "approval.expired"


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Event:
    event_type: EventType
    session_id: UUID | None = None
    turn_id: int | None = None
    step_id: int | None = None
    timestamp: datetime = field(default_factory=_utcnow)
    payload: dict[str, Any] = field(default_factory=dict)
    audit_id: UUID = field(default_factory=uuid4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id": str(self.audit_id),
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type.value,
            "session_id": str(self.session_id) if self.session_id else None,
            "turn_id": self.turn_id,
            "step_id": self.step_id,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        return cls(
            audit_id=UUID(d["audit_id"]),
            timestamp=datetime.fromisoformat(d["timestamp"]),
            event_type=EventType(d["event_type"]),
            session_id=UUID(d["session_id"]) if d.get("session_id") else None,
            turn_id=d.get("turn_id"),
            step_id=d.get("step_id"),
            payload=d.get("payload") or {},
        )
