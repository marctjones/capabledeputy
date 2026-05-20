"""Composition — Demo #3 surface integration.

The pure-function grouping is already tested in
test_approval_grouping.py. This file pins the runtime path: a
batch of ApprovalRequest objects (the operator's pending queue)
groups by `justification`, with N actions and M unique
justifications producing exactly M groups (SC-012).
"""

from __future__ import annotations

from dataclasses import dataclass

from capabledeputy.policy.approval_grouping import group_pending_approvals


@dataclass
class _FakeApprovalRequest:
    """Shape-compatible with approval.model.ApprovalRequest for the
    `group_pending_approvals` duck-typed interface."""

    action: str
    target: str
    justification: str


def test_500_actions_2_justifications_yields_2_groups() -> None:
    """SC-012 — the headline scenario."""
    pending = [
        _FakeApprovalRequest(
            action="email.send",
            target=f"user-{i}@example.com",
            justification=("monthly newsletter blast" if i % 2 == 0 else "invoice run"),
        )
        for i in range(500)
    ]
    grouped = group_pending_approvals(pending)
    assert grouped.count_groups == 2
    assert grouped.count_actions == 500
    rationales = {g.rationale for g in grouped.groups}
    assert rationales == {"monthly newsletter blast", "invoice run"}


def test_homogeneous_pending_yields_one_group() -> None:
    """All actions share one justification ⇒ one prompt."""
    pending = [
        _FakeApprovalRequest(
            action="email.send",
            target=f"user-{i}",
            justification="weekly digest",
        )
        for i in range(100)
    ]
    grouped = group_pending_approvals(pending)
    assert grouped.count_groups == 1
    assert grouped.groups[0].count == 100


def test_empty_pending_queue_yields_empty_set() -> None:
    grouped = group_pending_approvals([])
    assert grouped.count_groups == 0
    assert grouped.count_actions == 0
