"""Approval bundle — collect every gate in a workflow into one decision.

A bundle is the output of dry-running a programmatic workflow with the
collector that defers REQUIRE_APPROVAL gates instead of halting. The
user reviews the bundle as a unit:

  - All steps the workflow would execute, in order.
  - Which steps require approval; which would deny outright.
  - The labels in scope at each gate.
  - The verbatim args each approval gate would dispatch with.

A single approve action approves every gate in the bundle. A deny
rejects everything. Partial approval (`approve_subset`) is supported
but loud — the audit log records exactly which subset.

The bundle carries a `program_hash` of the source code that produced
it. Re-running the program with the bundle requires the source to be
byte-identical; otherwise the bundle is rejected (the program changed
between preview and execution and the user would be approving
something they didn't see).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4


class GateState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    WOULD_DENY = "would_deny"  # policy DENY at dry-run; never approvable


@dataclass(frozen=True)
class BundledApproval:
    """One gate inside a bundle. Frozen so the user can't tamper with
    the predicted args between review and execution."""

    step_index: int
    tool_name: str
    args: dict[str, Any]
    arg_labels: frozenset[str]
    rule: str | None
    reason: str | None
    state: GateState = GateState.PENDING

    def with_state(self, state: GateState) -> BundledApproval:
        from dataclasses import replace as _replace

        return _replace(self, state=state)


@dataclass(frozen=True)
class WorkflowStep:
    """One step in the predicted workflow — both ALLOW and approval-
    gated steps appear here so the impact view is complete."""

    step_index: int
    tool_name: str
    args: dict[str, Any]
    arg_labels: frozenset[str]
    decision: str  # 'allow' | 'deny' | 'require_approval'
    inherent_labels: frozenset[str]
    rule: str | None
    reason: str | None
    line: int | None


def hash_program(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


@dataclass
class WorkflowImpact:
    """Bundle returned by the dry-run collector.

    `steps` is the entire predicted execution; `gates` is the subset
    that need user attention (REQUIRE_APPROVAL or WOULD_DENY). The user
    UI typically renders `steps` as a tree and `gates` as the action
    list at the bottom.
    """

    bundle_id: UUID = field(default_factory=uuid4)
    program_hash: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    steps: list[WorkflowStep] = field(default_factory=list)
    gates: list[BundledApproval] = field(default_factory=list)
    parse_error: str | None = None
    runtime_error: str | None = None

    @property
    def has_blocking_deny(self) -> bool:
        """True if any step would unconditionally deny — bundle cannot
        be approved (the user said no to this rule at policy-authoring
        time)."""
        return any(g.state == GateState.WOULD_DENY for g in self.gates)

    @property
    def is_approvable(self) -> bool:
        return (
            self.parse_error is None
            and self.runtime_error is None
            and not self.has_blocking_deny
        )

    def approve_all(self) -> WorkflowImpact:
        """Mark every PENDING gate as APPROVED. Deny gates stay denied.
        Returns a new WorkflowImpact (the original is immutable in
        spirit; we keep gates as a fresh list so audits keep the
        before/after state intact)."""
        new_gates = [
            g.with_state(GateState.APPROVED)
            if g.state == GateState.PENDING
            else g
            for g in self.gates
        ]
        return WorkflowImpact(
            bundle_id=self.bundle_id,
            program_hash=self.program_hash,
            created_at=self.created_at,
            steps=self.steps,
            gates=new_gates,
        )

    def deny_all(self) -> WorkflowImpact:
        new_gates = [
            g.with_state(GateState.DENIED)
            if g.state == GateState.PENDING
            else g
            for g in self.gates
        ]
        return WorkflowImpact(
            bundle_id=self.bundle_id,
            program_hash=self.program_hash,
            created_at=self.created_at,
            steps=self.steps,
            gates=new_gates,
        )

    def gate_for(self, step_index: int, tool_name: str) -> BundledApproval | None:
        for g in self.gates:
            if g.step_index == step_index and g.tool_name == tool_name:
                return g
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle_id": str(self.bundle_id),
            "program_hash": self.program_hash,
            "created_at": self.created_at.isoformat(),
            "steps": [
                {
                    "step_index": s.step_index,
                    "tool": s.tool_name,
                    "args": s.args,
                    "arg_labels": sorted(s.arg_labels),
                    "decision": s.decision,
                    "inherent_labels": sorted(s.inherent_labels),
                    "rule": s.rule,
                    "reason": s.reason,
                    "line": s.line,
                }
                for s in self.steps
            ],
            "gates": [
                {
                    "step_index": g.step_index,
                    "tool": g.tool_name,
                    "args": g.args,
                    "arg_labels": sorted(g.arg_labels),
                    "rule": g.rule,
                    "reason": g.reason,
                    "state": g.state.value,
                }
                for g in self.gates
            ],
        }


def render_impact_tree(impact: WorkflowImpact) -> str:
    """Human-readable rendering of the impact tree. Used by the CLI
    bundle-review subcommand and the audit log when a bundle is
    recorded.
    """
    if impact.parse_error:
        return f"PARSE ERROR: {impact.parse_error}"
    if impact.runtime_error:
        return f"RUNTIME ERROR: {impact.runtime_error}"
    if not impact.steps:
        return "(empty workflow)"

    lines = [f"Bundle {str(impact.bundle_id)[:8]} ({len(impact.steps)} step(s)):"]
    for s in impact.steps:
        symbol = {
            "allow": "✓",
            "require_approval": "⚠",
            "deny": "✗",
        }.get(s.decision, "?")
        labels = ",".join(sorted(s.arg_labels)) or "-"
        rule = f" rule={s.rule}" if s.rule else ""
        lines.append(
            f"  {symbol} [{s.step_index:>2}] {s.tool_name}"
            f" labels={labels}{rule}",
        )
    if impact.gates:
        approval_count = sum(
            1 for g in impact.gates if g.state == GateState.PENDING
        )
        deny_count = sum(
            1 for g in impact.gates if g.state == GateState.WOULD_DENY
        )
        lines.append("")
        lines.append(
            f"  {approval_count} approval gate(s) pending, "
            f"{deny_count} non-negotiable deny(s).",
        )
    return "\n".join(lines)
