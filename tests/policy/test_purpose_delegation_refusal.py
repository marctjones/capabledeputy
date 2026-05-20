"""T053 — Delegation refused on inadmissible category (FR-009).

Extends 002 US1 delegation: even an attenuation-compliant delegation
request is refused if it would introduce a category the child
session's purpose does not admit. The refusal reason is
`INADMISSIBLE_CATEGORY` so audits and operators can distinguish it
from the existing 002 refusal taxonomy.
"""

from __future__ import annotations

import pytest

from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
    DelegationRefusal,
    DelegationRefusalReason,
    DelegationRequest,
)
from capabledeputy.policy.purposes import Purpose, Purposes
from capabledeputy.session.graph import SessionGraph


def _registry() -> Purposes:
    return Purposes(
        purposes={
            "employee-evaluation": Purpose(
                purpose_id="employee-evaluation",
                admissible_categories=frozenset({"work_performance"}),
                inadmissible_categories=frozenset({"health"}),
            ),
            "general": Purpose(
                purpose_id="general",
                admissible_categories=frozenset(
                    {"work_performance", "health", "personal"},
                ),
            ),
        },
    )


def _parent_cap() -> Capability:
    return Capability(
        kind=CapabilityKind.READ_FS,
        pattern="/data/*",
        origin=CapabilityOrigin.USER_APPROVED,
    )


@pytest.mark.asyncio
async def test_delegation_with_admissible_category_succeeds() -> None:
    graph = SessionGraph(purposes=_registry())
    parent = await graph.new(purpose_handle="general")
    child = await graph.new(parent=parent.id, purpose_handle="employee-evaluation")
    await graph.grant_capability(parent.id, _parent_cap())
    result = await graph.delegate(
        parent.id,
        child.id,
        DelegationRequest(kind=CapabilityKind.READ_FS, pattern="/data/perf/*"),
        depth_limit=3,
        categories=frozenset({"work_performance"}),
    )
    assert isinstance(result, Capability)
    assert result.kind == CapabilityKind.READ_FS


@pytest.mark.asyncio
async def test_delegation_with_inadmissible_category_refused() -> None:
    """Child's purpose=`employee-evaluation` does NOT admit `health`.
    Even with a valid attenuation, the delegation must be refused
    with reason INADMISSIBLE_CATEGORY (T059)."""
    graph = SessionGraph(purposes=_registry())
    parent = await graph.new(purpose_handle="general")
    child = await graph.new(parent=parent.id, purpose_handle="employee-evaluation")
    await graph.grant_capability(parent.id, _parent_cap())
    result = await graph.delegate(
        parent.id,
        child.id,
        DelegationRequest(kind=CapabilityKind.READ_FS, pattern="/data/medical/*"),
        depth_limit=3,
        categories=frozenset({"health"}),
    )
    assert isinstance(result, DelegationRefusal)
    assert result.reason == DelegationRefusalReason.INADMISSIBLE_CATEGORY


@pytest.mark.asyncio
async def test_delegation_to_unset_child_refused() -> None:
    """Child has `unset` purpose; any categorized delegation refused
    (FR-046 fail-closed)."""
    graph = SessionGraph(purposes=_registry())
    parent = await graph.new(purpose_handle="general")
    child = await graph.new(parent=parent.id)  # default unset
    await graph.grant_capability(parent.id, _parent_cap())
    result = await graph.delegate(
        parent.id,
        child.id,
        DelegationRequest(kind=CapabilityKind.READ_FS, pattern="/data/perf/*"),
        depth_limit=3,
        categories=frozenset({"work_performance"}),
    )
    assert isinstance(result, DelegationRefusal)
    assert result.reason == DelegationRefusalReason.INADMISSIBLE_CATEGORY


@pytest.mark.asyncio
async def test_delegation_categoryless_still_works() -> None:
    """A delegation with no declared categories does not trigger the
    purpose check (back-compat with 002 delegation tests)."""
    graph = SessionGraph(purposes=_registry())
    parent = await graph.new(purpose_handle="general")
    child = await graph.new(parent=parent.id, purpose_handle="employee-evaluation")
    await graph.grant_capability(parent.id, _parent_cap())
    result = await graph.delegate(
        parent.id,
        child.id,
        DelegationRequest(kind=CapabilityKind.READ_FS, pattern="/data/perf/*"),
        depth_limit=3,
        # categories omitted ⇒ legacy 002 delegation semantics
    )
    assert isinstance(result, Capability)


@pytest.mark.asyncio
async def test_delegation_without_purposes_registry_refuses_categorized() -> None:
    """If the graph has no Purposes registry, any categorized
    delegation is refused INADMISSIBLE_CATEGORY (fail-closed)."""
    graph = SessionGraph()  # no purposes registry
    parent = await graph.new()
    child = await graph.new(parent=parent.id)
    await graph.grant_capability(parent.id, _parent_cap())
    result = await graph.delegate(
        parent.id,
        child.id,
        DelegationRequest(kind=CapabilityKind.READ_FS, pattern="/data/perf/*"),
        depth_limit=3,
        categories=frozenset({"work_performance"}),
    )
    assert isinstance(result, DelegationRefusal)
    assert result.reason == DelegationRefusalReason.INADMISSIBLE_CATEGORY
