"""T051 — Session without a purpose_handle is fail-closed (FR-046 / SC-020).

A session whose purpose_handle is the default sentinel (`unset`) —
i.e. no explicit purpose at spawn — admits no consequential effects.
Any subsequent capability grant that declares a data category must
be refused. This is the structural guarantee behind FR-046.

The negative invariant tested here: a session can be spawned with
`unset`, but it cannot subsequently be granted a category-bearing
capability without raising PurposeAdmissibilityError.
"""

from __future__ import annotations

import pytest

from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.purposes import (
    UNSET_PURPOSE_HANDLE,
    Purpose,
    Purposes,
)
from capabledeputy.session.graph import (
    PurposeAdmissibilityError,
    SessionGraph,
)


def _registry() -> Purposes:
    return Purposes(
        purposes={
            "employee-evaluation": Purpose(
                purpose_id="employee-evaluation",
                admissible_categories=frozenset({"work_performance"}),
                inadmissible_categories=frozenset({"health", "personal"}),
            ),
        },
    )


def _read_fs_cap() -> Capability:
    return Capability(
        kind=CapabilityKind.READ_FS,
        pattern="*",
        origin=CapabilityOrigin.USER_APPROVED,
    )


@pytest.mark.asyncio
async def test_unset_purpose_default_at_spawn() -> None:
    graph = SessionGraph(purposes=_registry())
    s = await graph.new()
    assert s.purpose_handle == UNSET_PURPOSE_HANDLE


@pytest.mark.asyncio
async def test_unset_session_refuses_category_bearing_grant() -> None:
    graph = SessionGraph(purposes=_registry())
    s = await graph.new()  # purpose defaults to 'unset'
    with pytest.raises(PurposeAdmissibilityError) as exc:
        await graph.grant_capability(
            s.id,
            _read_fs_cap(),
            categories=frozenset({"work_performance"}),
        )
    assert exc.value.purpose_handle == UNSET_PURPOSE_HANDLE
    assert exc.value.inadmissible_categories == frozenset({"work_performance"})


@pytest.mark.asyncio
async def test_unset_session_allows_categoryless_grant() -> None:
    """A grant without declared categories is fine — that's the
    legacy capability flow (FR-046 only fires when categories are
    declared at grant time)."""
    graph = SessionGraph(purposes=_registry())
    s = await graph.new()
    updated = await graph.grant_capability(s.id, _read_fs_cap())
    assert _read_fs_cap() in updated.capability_set or any(
        c.kind == CapabilityKind.READ_FS for c in updated.capability_set
    )


@pytest.mark.asyncio
async def test_spawn_with_inadmissible_candidate_categories_refuses() -> None:
    """T056 — if the caller declares candidate_capability_categories at
    spawn, the admissibility check fires BEFORE the session exists.
    employee-evaluation does not admit `health`, so the spawn is
    refused."""
    graph = SessionGraph(purposes=_registry())
    with pytest.raises(PurposeAdmissibilityError) as exc:
        await graph.new(
            purpose_handle="employee-evaluation",
            candidate_capability_categories=frozenset({"health"}),
        )
    assert exc.value.inadmissible_categories == frozenset({"health"})
    assert len(graph) == 0  # no session was created


@pytest.mark.asyncio
async def test_spawn_with_admissible_candidate_categories_succeeds() -> None:
    graph = SessionGraph(purposes=_registry())
    s = await graph.new(
        purpose_handle="employee-evaluation",
        candidate_capability_categories=frozenset({"work_performance"}),
    )
    assert s.purpose_handle == "employee-evaluation"


@pytest.mark.asyncio
async def test_no_purposes_registry_refuses_any_categorized_grant() -> None:
    """SessionGraph with no Purposes registry ⇒ no purpose admits
    anything ⇒ every categorized grant refused. The hardest fail-
    closed surface (FR-046)."""
    graph = SessionGraph()  # purposes=None
    s = await graph.new()
    with pytest.raises(PurposeAdmissibilityError):
        await graph.grant_capability(
            s.id,
            _read_fs_cap(),
            categories=frozenset({"anything"}),
        )
