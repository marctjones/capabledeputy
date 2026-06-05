"""Cookbook P2.3 — RelationshipGroups mutation surface + YAML
round-trip + dispatcher integration.

Covers:
  - resolve(principal_id) returns groups
  - add_member creates absent groups, no-ops on existing membership
  - remove_member tracks state correctly
  - save/load round-trips preserve membership
  - save preserves leading comment header
  - dispatcher merges resolved counterparty groups into axis_d
    so the family-personal-email-suggest rule actually fires for
    spouse@example.com after add_member
"""

from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.policy.relationships import (
    RelationshipGroup,
    RelationshipGroups,
    load,
    save,
)


# --- resolve / add_member / remove_member --------------------------------


def test_resolve_returns_all_group_memberships() -> None:
    rg = RelationshipGroups(
        groups={
            "family": RelationshipGroup(
                group_id="family",
                member_principal_ids=frozenset({"spouse@x.com", "kid@x.com"}),
            ),
            "work-team": RelationshipGroup(
                group_id="work-team",
                member_principal_ids=frozenset({"coworker@x.com"}),
            ),
        },
    )
    assert rg.resolve("spouse@x.com") == frozenset({"family"})
    assert rg.resolve("coworker@x.com") == frozenset({"work-team"})
    assert rg.resolve("stranger@x.com") == frozenset()


def test_resolve_returns_multiple_groups() -> None:
    """A principal can belong to multiple groups — boss could be
    both family AND work-team."""
    rg = RelationshipGroups(
        groups={
            "family": RelationshipGroup(
                group_id="family",
                member_principal_ids=frozenset({"alex@x.com"}),
            ),
            "work-team": RelationshipGroup(
                group_id="work-team",
                member_principal_ids=frozenset({"alex@x.com"}),
            ),
        },
    )
    assert rg.resolve("alex@x.com") == frozenset({"family", "work-team"})


def test_add_member_to_existing_group() -> None:
    rg = RelationshipGroups(
        groups={
            "family": RelationshipGroup(
                group_id="family",
                member_principal_ids=frozenset({"spouse@x.com"}),
            ),
        },
    )
    assert rg.add_member("family", "kid@x.com") is True
    assert "kid@x.com" in rg.groups["family"].member_principal_ids


def test_add_member_creates_group_when_absent() -> None:
    """Auto-narrowing case: operator picks a brand-new group name in
    the chat REPL ('book-club'); add_member mints it."""
    rg = RelationshipGroups(groups={})
    assert rg.add_member("book-club", "alice@example.com") is True
    assert "book-club" in rg.groups
    assert rg.groups["book-club"].member_principal_ids == frozenset(
        {"alice@example.com"},
    )


def test_add_member_idempotent_when_already_member() -> None:
    rg = RelationshipGroups(
        groups={
            "family": RelationshipGroup(
                group_id="family",
                member_principal_ids=frozenset({"spouse@x.com"}),
            ),
        },
    )
    assert rg.add_member("family", "spouse@x.com") is False


def test_remove_member_present_and_absent() -> None:
    rg = RelationshipGroups(
        groups={
            "family": RelationshipGroup(
                group_id="family",
                member_principal_ids=frozenset({"spouse@x.com", "kid@x.com"}),
            ),
        },
    )
    assert rg.remove_member("family", "kid@x.com") is True
    assert "kid@x.com" not in rg.groups["family"].member_principal_ids
    # Removing a non-member is False
    assert rg.remove_member("family", "stranger@x.com") is False
    # Removing from non-existent group is False
    assert rg.remove_member("not-a-group", "anyone@x.com") is False


# --- YAML persistence ----------------------------------------------------


def test_save_then_load_round_trip(tmp_path: Path) -> None:
    """A registry written via save() reloads via load() with the
    same memberships. Sorted output → stable diffs."""
    rg = RelationshipGroups(
        groups={
            "family": RelationshipGroup(
                group_id="family",
                member_principal_ids=frozenset({"spouse@x.com", "kid@x.com"}),
            ),
            "work-team": RelationshipGroup(
                group_id="work-team",
                member_principal_ids=frozenset({"coworker@x.com"}),
            ),
        },
    )
    path = tmp_path / "relationship_groups.yaml"
    # save() reads the existing file's header — empty file is fine.
    path.write_text("# my header\n\n", encoding="utf-8")
    save(rg, path)
    reloaded = load(path)
    assert sorted(reloaded.groups) == ["family", "work-team"]
    assert reloaded.groups["family"].member_principal_ids == frozenset(
        {"spouse@x.com", "kid@x.com"},
    )
    assert reloaded.groups["work-team"].member_principal_ids == frozenset(
        {"coworker@x.com"},
    )


def test_save_preserves_leading_comment_header(tmp_path: Path) -> None:
    """The operator-authored header block in
    configs/relationship_groups.yaml (license, FR references,
    instructions) must survive save()."""
    path = tmp_path / "relationship_groups.yaml"
    path.write_text(
        "# 003 Relationship Groups (FR-033).\n# Human-declared groupings.\n\ngroups: []\n",
        encoding="utf-8",
    )
    rg = RelationshipGroups(
        groups={
            "family": RelationshipGroup(
                group_id="family",
                member_principal_ids=frozenset({"spouse@x.com"}),
            ),
        },
    )
    save(rg, path)
    text = path.read_text(encoding="utf-8")
    assert "# 003 Relationship Groups (FR-033)." in text
    assert "# Human-declared groupings." in text
    assert "groups:" in text
    assert "spouse@x.com" in text


def test_save_empty_registry_writes_explicit_empty_list(
    tmp_path: Path,
) -> None:
    """A registry with no groups writes `groups: []` (operator-
    readable) rather than `groups:` with nothing after it."""
    path = tmp_path / "relationship_groups.yaml"
    path.write_text("# header\n", encoding="utf-8")
    save(RelationshipGroups(groups={}), path)
    text = path.read_text(encoding="utf-8")
    assert "groups: []" in text
    # Round-trip still works
    reloaded = load(path)
    assert reloaded.groups == {}


# --- Dispatcher integration: target → groups → rule fires ---------------


def test_resolver_threads_into_axis_d_via_tool_client(tmp_path: Path) -> None:
    """End-to-end: a session sending mail to spouse@x.com should
    have `relationship_group_ids` containing 'family' in axis_d by
    the time decide() is called — even though session.axis_d
    doesn't carry that membership statically. This is what makes
    the family-personal-email-suggest rule actually fire in
    production. Tests _resolve_action_axis_d directly."""
    from dataclasses import dataclass
    from types import SimpleNamespace

    from capabledeputy.policy.axis_d import DecisionContext
    from capabledeputy.tools.client import LabeledToolClient, PolicyContext

    rg = RelationshipGroups(
        groups={
            "family": RelationshipGroup(
                group_id="family",
                member_principal_ids=frozenset({"spouse@x.com"}),
            ),
        },
    )
    pc = PolicyContext(relationship_groups=rg)
    # Construct a minimal LabeledToolClient — we only need
    # _resolve_action_axis_d, which doesn't touch registry/graph.
    client = LabeledToolClient(
        registry=None,  # type: ignore[arg-type]
        graph=None,
        audit=None,
        policy_context=pc,
    )

    @dataclass
    class _FakeAction:
        target: str

    session_axis_d = DecisionContext(counterparty="spouse@x.com")
    out = client._resolve_action_axis_d(
        session_axis_d,
        action=_FakeAction(target="spouse@x.com"),
    )
    assert "family" in out.relationship_group_ids


def test_resolver_noop_when_no_registry_wired() -> None:
    """When no RelationshipGroups is wired on the policy context,
    the axis_d passes through unchanged — no crash, no mutation."""
    from dataclasses import dataclass

    from capabledeputy.policy.axis_d import DecisionContext
    from capabledeputy.tools.client import LabeledToolClient, PolicyContext

    pc = PolicyContext()  # no relationship_groups
    client = LabeledToolClient(
        registry=None,  # type: ignore[arg-type]
        graph=None,
        audit=None,
        policy_context=pc,
    )

    @dataclass
    class _FakeAction:
        target: str

    session_axis_d = DecisionContext(counterparty="spouse@x.com")
    out = client._resolve_action_axis_d(
        session_axis_d,
        action=_FakeAction(target="spouse@x.com"),
    )
    assert out is session_axis_d  # unchanged


def test_resolver_preserves_existing_session_memberships() -> None:
    """If the session already carries explicit relationship_group_ids
    (e.g. operator forced them via /session-attrs), the resolver
    WIDENS rather than replaces. Existing memberships are
    preserved; resolved memberships are added on top."""
    from dataclasses import dataclass

    from capabledeputy.policy.axis_d import DecisionContext
    from capabledeputy.tools.client import LabeledToolClient, PolicyContext

    rg = RelationshipGroups(
        groups={
            "family": RelationshipGroup(
                group_id="family",
                member_principal_ids=frozenset({"spouse@x.com"}),
            ),
        },
    )
    pc = PolicyContext(relationship_groups=rg)
    client = LabeledToolClient(
        registry=None,  # type: ignore[arg-type]
        graph=None,
        audit=None,
        policy_context=pc,
    )

    @dataclass
    class _FakeAction:
        target: str

    session_axis_d = DecisionContext(
        counterparty="spouse@x.com",
        relationship_group_ids=frozenset({"some-other-group"}),
    )
    out = client._resolve_action_axis_d(
        session_axis_d,
        action=_FakeAction(target="spouse@x.com"),
    )
    assert "family" in out.relationship_group_ids
    assert "some-other-group" in out.relationship_group_ids


# --- RPC handler ---------------------------------------------------------


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_add_member_rpc_persists_to_yaml(tmp_path: Path) -> None:
    """The relationship_group.add_member RPC mutates in-memory AND
    writes to relationship_groups.yaml so the change survives
    daemon restart."""
    from capabledeputy.daemon.relationship_handlers import (
        make_relationship_handlers,
    )
    from capabledeputy.tools.client import PolicyContext

    path = tmp_path / "relationship_groups.yaml"
    path.write_text("# header\ngroups: []\n", encoding="utf-8")
    rg = RelationshipGroups(groups={})

    class _App:
        policy_context = PolicyContext(
            relationship_groups=rg,
            relationship_groups_path=path,
        )

    handlers = make_relationship_handlers(_App())
    result = await handlers["relationship_group.add_member"](
        {"group_id": "family", "principal_id": "spouse@x.com"},
    )
    assert result["added"] is True
    assert result["persisted"] is True
    # File contains the addition
    assert "spouse@x.com" in path.read_text(encoding="utf-8")
    # In-memory registry updated
    assert "family" in rg.groups
    assert "spouse@x.com" in rg.groups["family"].member_principal_ids


@pytest.mark.anyio
async def test_add_member_rpc_idempotent_when_already_member(
    tmp_path: Path,
) -> None:
    """A second add_member with the same identity returns
    added=False; persisted=True because the file already reflects
    the state."""
    from capabledeputy.daemon.relationship_handlers import (
        make_relationship_handlers,
    )
    from capabledeputy.tools.client import PolicyContext

    path = tmp_path / "relationship_groups.yaml"
    path.write_text("# header\ngroups: []\n", encoding="utf-8")
    rg = RelationshipGroups(
        groups={
            "family": RelationshipGroup(
                group_id="family",
                member_principal_ids=frozenset({"spouse@x.com"}),
            ),
        },
    )

    class _App:
        policy_context = PolicyContext(
            relationship_groups=rg,
            relationship_groups_path=path,
        )

    handlers = make_relationship_handlers(_App())
    result = await handlers["relationship_group.add_member"](
        {"group_id": "family", "principal_id": "spouse@x.com"},
    )
    assert result["added"] is False
    assert result["persisted"] is True


@pytest.mark.anyio
async def test_list_rpc_returns_sorted_view(tmp_path: Path) -> None:
    """The list endpoint is the canonical read surface for the
    chat REPL's auto-narrowing picker. Returns groups sorted by
    group_id, members sorted by principal_id."""
    from capabledeputy.daemon.relationship_handlers import (
        make_relationship_handlers,
    )
    from capabledeputy.tools.client import PolicyContext

    rg = RelationshipGroups(
        groups={
            "work-team": RelationshipGroup(
                group_id="work-team",
                member_principal_ids=frozenset({"coworker@x.com", "boss@x.com"}),
            ),
            "family": RelationshipGroup(
                group_id="family",
                member_principal_ids=frozenset({"spouse@x.com"}),
            ),
        },
    )

    class _App:
        policy_context = PolicyContext(
            relationship_groups=rg,
            relationship_groups_path=tmp_path / "rg.yaml",
        )

    handlers = make_relationship_handlers(_App())
    result = await handlers["relationship_group.list"]({})
    gids = [g["group_id"] for g in result["groups"]]
    assert gids == ["family", "work-team"]
    members = [g["member_principal_ids"] for g in result["groups"]]
    assert members[0] == ["spouse@x.com"]
    assert members[1] == ["boss@x.com", "coworker@x.com"]


def test_make_relationship_handlers_empty_when_unwired() -> None:
    """No registry wired → empty handler dict. The chat REPL handles
    the missing endpoint gracefully (auto-narrowing prompt
    collapses)."""
    from capabledeputy.daemon.relationship_handlers import (
        make_relationship_handlers,
    )
    from capabledeputy.tools.client import PolicyContext

    class _App:
        policy_context = PolicyContext()  # no registry

    handlers = make_relationship_handlers(_App())
    assert handlers == {}
