"""T038 invariants for 003 US2 (FR-033).

Relationship group membership: declared by operator in
configs/relationship_groups.yaml; consulted by decision rules to
scope sharing/disclosure decisions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.policy.relationships import (
    RelationshipGroup,
    RelationshipGroupError,
    RelationshipGroups,
    load,
    principal_ids_from_target,
)


def test_is_member_basic() -> None:
    groups = RelationshipGroups(
        groups={
            "project-P": RelationshipGroup(
                group_id="project-P",
                member_principal_ids=frozenset({"alice", "bob"}),
            ),
        },
    )
    assert groups.is_member("alice", "project-P")
    assert groups.is_member("bob", "project-P")
    assert not groups.is_member("eve", "project-P")


def test_is_member_unknown_group_returns_false() -> None:
    """Unknown group ⇒ False (fail-closed). No KeyError."""
    groups = RelationshipGroups(groups={})
    assert not groups.is_member("alice", "ghost-group")


def test_share_proprietary_work_scenario(tmp_path: Path) -> None:
    """US2 scenario 5: share proprietary_work to project-P members.
    The membership predicate is the rule-engine input; this test
    only verifies the membership oracle behaves correctly."""
    (tmp_path / "rg.yaml").write_text(
        "groups:\n"
        "  - group_id: project-P\n"
        "    member_principal_ids: [alice, bob, marc]\n"
        "  - group_id: team-A\n"
        "    member_principal_ids: [diane, eve]\n",
    )
    groups = load(tmp_path / "rg.yaml")
    assert groups.is_member("alice", "project-P")
    assert not groups.is_member("alice", "team-A")
    assert not groups.is_member("frank", "project-P")


def test_principal_ids_from_target_extracts_embedded_email_principals() -> None:
    assert principal_ids_from_target(
        "gcal://calendar/primary/events/attendees/me@example.com,spouse@example.com",
    ) == frozenset(
        {
            "gcal://calendar/primary/events/attendees/me@example.com,spouse@example.com",
            "me@example.com",
            "spouse@example.com",
        },
    )


def test_resolve_target_uses_exact_and_embedded_principals() -> None:
    groups = RelationshipGroups(
        groups={
            "self": RelationshipGroup(
                group_id="self",
                member_principal_ids=frozenset({"me@example.com"}),
            ),
            "calendar-resource": RelationshipGroup(
                group_id="calendar-resource",
                member_principal_ids=frozenset({"gcal://calendar/primary"}),
            ),
        },
    )

    assert groups.resolve_target("gcal://calendar/primary") == frozenset(
        {"calendar-resource"},
    )
    assert groups.resolve_target(
        "gcal://calendar/primary/events/attendees/me@example.com",
    ) == frozenset({"self"})


def test_load_empty_yaml() -> None:
    """Empty file ⇒ empty registry, not error."""
    from tempfile import NamedTemporaryFile

    with NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("groups: []\n")
        path = Path(f.name)
    groups = load(path)
    assert len(groups.groups) == 0


def test_load_missing_file_fails_closed() -> None:
    with pytest.raises(RelationshipGroupError, match="missing"):
        load(Path("/nonexistent/rg.yaml"))


def test_load_malformed_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "bad.yaml").write_text("groups:\n  - no_group_id: x\n")
    with pytest.raises(RelationshipGroupError, match="group_id"):
        load(tmp_path / "bad.yaml")


def test_load_duplicate_group_id_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "dup.yaml").write_text(
        "groups:\n"
        "  - group_id: project-P\n"
        "    member_principal_ids: [a]\n"
        "  - group_id: project-P\n"
        "    member_principal_ids: [b]\n",
    )
    with pytest.raises(RelationshipGroupError, match="duplicate"):
        load(tmp_path / "dup.yaml")
