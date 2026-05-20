"""T071 — Semantic approval grouping (FR-035 / SC-012).

500 homogeneous actions with 2 distinct rationales ⇒ exactly 2
approval groups. Per-step prompting is structurally forbidden by
requiring a non-empty rationale on every action.
"""

from __future__ import annotations

import pytest

from capabledeputy.policy.approval_grouping import (
    ApprovableAction,
    ApprovalGroupingError,
    group_by_rationale,
)


def test_500_actions_2_rationales_yields_2_groups() -> None:
    """SC-012 — the headline scenario."""
    actions = tuple(
        ApprovableAction(
            action_kind="email.send",
            target=f"customer-{i}@example.com",
            rationale="batch send 'monthly newsletter'" if i % 2 == 0 else "batch send 'invoice'",
            estimated_impact="external email",
        )
        for i in range(500)
    )
    grouped = group_by_rationale(actions)
    assert grouped.count_groups == 2
    assert grouped.count_actions == 500
    assert {g.rationale for g in grouped.groups} == {
        "batch send 'monthly newsletter'",
        "batch send 'invoice'",
    }


def test_missing_rationale_refused() -> None:
    """An action with an empty rationale refuses (FR-035 — never
    approve an action with no stated reason)."""
    actions = (
        ApprovableAction(
            action_kind="email.send",
            target="alice@example.com",
            rationale="",
        ),
    )
    with pytest.raises(ApprovalGroupingError):
        group_by_rationale(actions)


def test_whitespace_rationale_refused() -> None:
    actions = (
        ApprovableAction(
            action_kind="email.send",
            target="alice@example.com",
            rationale="   ",
        ),
    )
    with pytest.raises(ApprovalGroupingError):
        group_by_rationale(actions)


def test_aggregated_impact_when_all_same() -> None:
    actions = tuple(
        ApprovableAction(
            action_kind="email.send",
            target=f"user-{i}@example.com",
            rationale="batch newsletter",
            estimated_impact="external email",
        )
        for i in range(3)
    )
    grouped = group_by_rationale(actions)
    assert grouped.groups[0].aggregated_impact == "3x external email"


def test_aggregated_impact_when_mixed() -> None:
    actions = (
        ApprovableAction(
            action_kind="email.send",
            target="a",
            rationale="shared",
            estimated_impact="external email",
        ),
        ApprovableAction(
            action_kind="post.publish",
            target="b",
            rationale="shared",
            estimated_impact="public post",
        ),
    )
    grouped = group_by_rationale(actions)
    assert "mixed" in grouped.groups[0].aggregated_impact


def test_group_order_is_first_occurrence() -> None:
    """Determinism: groups are ordered by first-occurrence of the
    rationale. Crucial for SC-002 audit replay."""
    actions = (
        ApprovableAction(action_kind="a", target="t1", rationale="R2"),
        ApprovableAction(action_kind="a", target="t2", rationale="R1"),
        ApprovableAction(action_kind="a", target="t3", rationale="R2"),
    )
    grouped = group_by_rationale(actions)
    assert [g.rationale for g in grouped.groups] == ["R2", "R1"]


def test_single_action_yields_single_group() -> None:
    grouped = group_by_rationale(
        (ApprovableAction(action_kind="a", target="t", rationale="just one"),),
    )
    assert grouped.count_groups == 1
    assert grouped.count_actions == 1


def test_empty_input_yields_empty_set() -> None:
    grouped = group_by_rationale(())
    assert grouped.count_groups == 0
    assert grouped.count_actions == 0
