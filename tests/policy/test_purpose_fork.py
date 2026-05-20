"""T052 — `SessionGraph.fork()` preserves parent `purpose_handle`.

Forking does not change the purpose under which a session is
operating — the child must inherit it. Otherwise an admissible
fork could be used to launder an inadmissible capability into a
session whose effective purpose silently differs from its parent.
"""

from __future__ import annotations

import pytest

from capabledeputy.policy.purposes import (
    UNSET_PURPOSE_HANDLE,
    Purpose,
    Purposes,
)
from capabledeputy.session.graph import SessionGraph


def _registry() -> Purposes:
    return Purposes(
        purposes={
            "employee-evaluation": Purpose(
                purpose_id="employee-evaluation",
                admissible_categories=frozenset({"work_performance"}),
            ),
        },
    )


@pytest.mark.asyncio
async def test_fork_preserves_explicit_purpose() -> None:
    graph = SessionGraph(purposes=_registry())
    parent = await graph.new(purpose_handle="employee-evaluation")
    child = await graph.fork(parent.id)
    assert child.purpose_handle == "employee-evaluation"


@pytest.mark.asyncio
async def test_fork_preserves_unset_purpose() -> None:
    """A session that was spawned with the default 'unset' purpose
    must also fork to 'unset' — not silently upgraded."""
    graph = SessionGraph(purposes=_registry())
    parent = await graph.new()
    child = await graph.fork(parent.id)
    assert child.purpose_handle == UNSET_PURPOSE_HANDLE


@pytest.mark.asyncio
async def test_fork_does_not_re_check_admissibility() -> None:
    """fork() does not re-run admissibility against candidate
    categories — it simply copies the parent's state. The parent's
    grants were already gated; the child inherits them as-is."""
    graph = SessionGraph(purposes=_registry())
    parent = await graph.new(purpose_handle="employee-evaluation")
    child = await graph.fork(parent.id)
    # Both share the same purpose; nothing is refused; child exists.
    assert child.id != parent.id
    assert child.parent == parent.id
    assert child.purpose_handle == parent.purpose_handle
