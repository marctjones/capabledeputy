"""Approval bundle — collect every gate in a workflow into one decision.

Roadmap v2 #6 — bundles carry an `expires_at` so a ratified
bundle can't sit on disk and fire stale weeks later. Default 24h,
configurable via `CAPDEP_BUNDLE_TTL_SECONDS`. `BundleExpiredError`
is the typed refusal raised at dispatch when execution arrives
after the deadline. Set CAPDEP_BUNDLE_TTL_SECONDS=0 for legacy /
explicit immortal bundles.

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
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from capabledeputy.policy.labels import LabelState

# Bundle wire format version. v1 carried only the lossy flat
# category/level-string fields (`inherent_labels`/`arg_labels`), which
# drop tier + risk_ids and merge categories with provenance levels into
# one set. v2 ADDS the structured four-axis `inherent_tags`/`arg_tags`
# (`LabelState`) alongside them (additive — the flat fields stay for
# back-compat). Readers prefer the structured fields when present and
# fall back to the flat strings for v1 bundles.
BUNDLE_FORMAT_VERSION = 2

# Roadmap v2 #6 — default bundle TTL in seconds. 24 hours unless
# overridden. Set to 0 (or empty string) to produce immortal
# bundles (back-compat / explicit operator choice).
_DEFAULT_BUNDLE_TTL_SECONDS = 86400


def _resolved_bundle_ttl_seconds() -> int:
    """Resolve the effective bundle TTL from env. We call this at
    bundle-creation time (not module import) so tests can override
    the env between runs without re-importing."""
    raw = os.environ.get("CAPDEP_BUNDLE_TTL_SECONDS")
    if raw is None or raw == "":
        return _DEFAULT_BUNDLE_TTL_SECONDS
    try:
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_BUNDLE_TTL_SECONDS


class BundleExpiredError(RuntimeError):
    """Raised when execute_with_approved_bundle is invoked after
    the bundle's `expires_at`. The operator should re-run dry_run
    to get a fresh bundle and re-approve — the policy / source
    may have shifted between ratification and execution, and the
    bundle's labels-in-scope snapshot is no longer authoritative.

    Carries the bundle id and the deadline so the operator can
    distinguish a stale bundle from a malformed one in the audit
    trail. Typed exception (not ValueError) so the daemon's RPC
    layer can surface a clean `bundle_expired` error code.
    """

    def __init__(self, bundle_id: UUID, expires_at: datetime) -> None:
        super().__init__(
            f"bundle {str(bundle_id)[:8]} expired at "
            f"{expires_at.isoformat()} — re-run dry_run for a fresh bundle",
        )
        self.bundle_id = bundle_id
        self.expires_at = expires_at


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
    # v2 structured arg taint (four-axis); preserves tier + risk_ids the
    # flat `arg_labels` strings drop. Empty for v1 bundles read back.
    arg_tags: LabelState = field(default_factory=LabelState)
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
    # v2 structured four-axis taint (preserves tier + risk_ids + the
    # category/provenance distinction the flat *_labels strings lose).
    # Empty for v1 bundles read back through from_dict.
    inherent_tags: LabelState = field(default_factory=LabelState)
    arg_tags: LabelState = field(default_factory=LabelState)


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
    # Roadmap v2 #6 — stale-bundle TTL (cookbook §6 T14). Computed
    # at dry-run time as created_at + CAPDEP_BUNDLE_TTL_SECONDS
    # when the env var is unset / non-zero. None ⇒ immortal
    # (legacy / explicit operator opt-out via env=0). Dispatch
    # raises BundleExpiredError when execution arrives past the
    # deadline.
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        """Populate expires_at from the resolved TTL when the
        caller didn't supply one. Skipped when expires_at is
        explicitly None AND the env disables TTL (TTL=0)."""
        if self.expires_at is not None:
            return
        ttl = _resolved_bundle_ttl_seconds()
        if ttl > 0:
            object.__setattr__(self, "expires_at", self.created_at + timedelta(seconds=ttl))

    def is_expired(self, *, now: datetime | None = None) -> bool:
        """True when the bundle's deadline has passed. `now`
        injectable so the dispatcher and tests can pin the clock.
        An immortal bundle (expires_at is None) is never expired."""
        if self.expires_at is None:
            return False
        check = now if now is not None else datetime.now(UTC)
        return check >= self.expires_at

    @property
    def has_blocking_deny(self) -> bool:
        """True if any step would unconditionally deny — bundle cannot
        be approved (the user said no to this rule at policy-authoring
        time)."""
        return any(g.state == GateState.WOULD_DENY for g in self.gates)

    @property
    def is_approvable(self) -> bool:
        return (
            self.parse_error is None and self.runtime_error is None and not self.has_blocking_deny
        )

    def approve_all(self) -> WorkflowImpact:
        """Mark every PENDING gate as APPROVED. Deny gates stay denied.
        Returns a new WorkflowImpact (the original is immutable in
        spirit; we keep gates as a fresh list so audits keep the
        before/after state intact)."""
        new_gates = [
            g.with_state(GateState.APPROVED) if g.state == GateState.PENDING else g
            for g in self.gates
        ]
        return WorkflowImpact(
            bundle_id=self.bundle_id,
            program_hash=self.program_hash,
            created_at=self.created_at,
            steps=self.steps,
            gates=new_gates,
            expires_at=self.expires_at,
        )

    def deny_all(self) -> WorkflowImpact:
        new_gates = [
            g.with_state(GateState.DENIED) if g.state == GateState.PENDING else g
            for g in self.gates
        ]
        return WorkflowImpact(
            bundle_id=self.bundle_id,
            program_hash=self.program_hash,
            created_at=self.created_at,
            steps=self.steps,
            gates=new_gates,
            expires_at=self.expires_at,
        )

    def gate_for(self, step_index: int, tool_name: str) -> BundledApproval | None:
        for g in self.gates:
            if g.step_index == step_index and g.tool_name == tool_name:
                return g
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": BUNDLE_FORMAT_VERSION,
            "bundle_id": str(self.bundle_id),
            "program_hash": self.program_hash,
            "created_at": self.created_at.isoformat(),
            "expires_at": (self.expires_at.isoformat() if self.expires_at is not None else None),
            "steps": [
                {
                    "step_index": s.step_index,
                    "tool": s.tool_name,
                    "args": s.args,
                    "arg_labels": sorted(s.arg_labels),
                    "decision": s.decision,
                    "inherent_labels": sorted(s.inherent_labels),
                    # v2 structured four-axis taint (lossless).
                    "inherent_tags": s.inherent_tags.to_dict(),
                    "arg_tags": s.arg_tags.to_dict(),
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
                    "arg_tags": g.arg_tags.to_dict(),
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

    header = f"Bundle {str(impact.bundle_id)[:8]} ({len(impact.steps)} step(s))"
    if impact.expires_at is not None:
        header += f"  expires {impact.expires_at.isoformat()}"
    lines = [header + ":"]
    for s in impact.steps:
        symbol = {
            "allow": "✓",
            "require_approval": "⚠",
            "deny": "✗",
        }.get(s.decision, "?")
        labels = ",".join(sorted(s.arg_labels)) or "-"
        rule = f" rule={s.rule}" if s.rule else ""
        lines.append(
            f"  {symbol} [{s.step_index:>2}] {s.tool_name} labels={labels}{rule}",
        )
    if impact.gates:
        approval_count = sum(1 for g in impact.gates if g.state == GateState.PENDING)
        deny_count = sum(1 for g in impact.gates if g.state == GateState.WOULD_DENY)
        lines.append("")
        lines.append(
            f"  {approval_count} approval gate(s) pending, {deny_count} non-negotiable deny(s).",
        )
    return "\n".join(lines)
