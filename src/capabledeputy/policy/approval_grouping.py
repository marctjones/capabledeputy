"""Semantic approval grouping (003 US6 T082 / FR-035 / SC-012).

When the planner emits N homogeneous actions with M distinct
rationales, the operator sees EXACTLY M approval groups — not N
per-step prompts. Per-step prompting is structurally forbidden:
the approval surface insists on a non-empty `rationale` and groups
on it.

This module owns the pure grouping function. The UI/CLI surface
that converts groups into a single approval prompt lives elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class ApprovalGroupingError(RuntimeError):
    """A grouped action was missing a rationale, or rationale was
    empty. Per FR-035, every action MUST carry one — refuse silently
    is a vulnerability."""


@dataclass(frozen=True)
class ApprovableAction:
    """An action that, in isolation, would require approval. Carries
    the rationale the planner attached. The (kind, target) pair is
    informational; grouping is on rationale alone — homogeneous
    actions of different kinds can share a group if their rationale
    is the same."""

    action_kind: str
    target: str
    rationale: str
    estimated_impact: str = ""


@dataclass(frozen=True)
class ApprovalGroup:
    """One group of actions sharing a rationale. `count` is the total
    number of actions; `actions` is the per-step record kept for the
    audit log; the UI shows only the rationale + count + aggregated
    impact, never per-step prompts."""

    rationale: str
    actions: tuple[ApprovableAction, ...]
    aggregated_impact: str = ""

    @property
    def count(self) -> int:
        return len(self.actions)


@dataclass(frozen=True)
class ApprovalGroupSet:
    """Result of grouping a batch. `groups` ordered by first-occurrence
    of the rationale (deterministic across runs given the same input
    order)."""

    groups: tuple[ApprovalGroup, ...] = field(default_factory=tuple)

    @property
    def count_groups(self) -> int:
        return len(self.groups)

    @property
    def count_actions(self) -> int:
        return sum(g.count for g in self.groups)


def group_by_rationale(
    actions: tuple[ApprovableAction, ...],
) -> ApprovalGroupSet:
    """Group actions by their rationale string. Each unique rationale
    becomes a group. Order is first-occurrence; idempotent across
    repeated runs.

    Refuses if any action carries an empty/whitespace rationale —
    per-step prompting is forbidden, but so is approving an action
    with no stated reason (Principle V transparency).
    """
    for i, a in enumerate(actions):
        if not a.rationale.strip():
            raise ApprovalGroupingError(
                f"actions[{i}] missing rationale — FR-035 requires a non-empty "
                f"rationale on every approvable action",
            )
    by_rationale: dict[str, list[ApprovableAction]] = {}
    order: list[str] = []
    for a in actions:
        if a.rationale not in by_rationale:
            by_rationale[a.rationale] = []
            order.append(a.rationale)
        by_rationale[a.rationale].append(a)
    groups = tuple(
        ApprovalGroup(
            rationale=r,
            actions=tuple(by_rationale[r]),
            aggregated_impact=_aggregate_impact(by_rationale[r]),
        )
        for r in order
    )
    return ApprovalGroupSet(groups=groups)


def _aggregate_impact(actions: list[ApprovableAction]) -> str:
    impacts = [a.estimated_impact for a in actions if a.estimated_impact]
    if not impacts:
        return f"{len(actions)} action(s) — no impact estimate attached"
    if all(i == impacts[0] for i in impacts):
        return f"{len(actions)}x {impacts[0]}"
    return f"{len(actions)} mixed-impact actions: " + "; ".join(sorted(set(impacts)))
