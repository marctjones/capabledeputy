"""Task lifecycle — full CRUD on the personal task list.

Demonstrates the complete add → list → edit → complete → delete cycle
with the destructive-op gate firing on delete (no allows_destructive
on the cap) and the reversibility gate refusing it (DELETE_FS is
irreversible/external).

The point: even on the operator's own todo list, irreversible deletes
require explicit operator intent — the agent cannot silently drop
items.
"""

from __future__ import annotations

from typing import Any

import pytest

from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.context import PolicyContext
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier
from demos.scenarios._helpers import (
    ai,
    audit,
    demo_header,
    make_app,
    make_session,
    note,
    policy_outcome,
    step,
    tool,
    user,
)


@pytest.mark.asyncio
async def test_task_lifecycle_demo(tmp_path: Any) -> None:
    demo_header(
        "Task Lifecycle — full CRUD with destructive-op + reversibility",
        blurb=(
            "add → list → edit → complete → delete. The delete refuses "
            "unless the cap declares allows_destructive AND an envelope "
            "or operator approval handles the irreversible reversibility."
        ),
        models=(
            "destructive-op gate (MODIFY_FS / DELETE_FS)",
            "FR-019 reversibility on irreversible deletes",
        ),
        patterns=("standing-cap for low-risk mutations",),
    )

    ctx = PolicyContext()
    app = make_app(tmp_path, policy_context=ctx)
    await app.startup()
    s = await make_session(
        app,
        axis_a_categories=(("personal", Tier.SENSITIVE),),
        capabilities=frozenset(
            {
                Capability(
                    kind=CapabilityKind.CREATE_FS,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
                Capability(
                    kind=CapabilityKind.READ_FS,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
                # Standing allows_destructive cap for MODIFY_FS — the
                # operator pre-granted this so tasks.complete and
                # tasks.edit run without per-call prompts. Low-risk
                # mutations on personal state; deliberate, unattended.
                Capability(
                    kind=CapabilityKind.MODIFY_FS,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                    allows_destructive=True,
                ),
                # DELETE_FS WITHOUT allows_destructive: deliberate, so
                # the delete attempt routes through REQUIRE_APPROVAL
                # via the destructive-op gate AND DENY via reversibility.
                Capability(
                    kind=CapabilityKind.DELETE_FS,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
            },
        ),
    )

    step(1, "Add three tasks")
    user('"add: groceries, passport, dentist"')
    ids: list[str] = []
    for title in ("buy groceries", "renew passport", "call dentist"):
        ai(f"call tasks.add(title={title!r})")
        out = await app.tool_client.call_tool(s.id, "tasks.add", {"title": title})
        assert out.decision is Decision.ALLOW
        ids.append(out.output["id"])
    tool(f"tasks.add x 3 → ids = {[x[:6] for x in ids]}")

    step(2, "List the open tasks")
    ai("call tasks.list()")
    listed = await app.tool_client.call_tool(s.id, "tasks.list", {})
    assert listed.decision is Decision.ALLOW
    policy_outcome(listed)
    tool(f"tasks.list → {[t['title'] for t in listed.output['tasks']]}")

    step(3, "Edit a task (rename)")
    ai(f"call tasks.edit(id={ids[0][:6]}…, title='buy groceries + flowers')")
    edit = await app.tool_client.call_tool(
        s.id,
        "tasks.edit",
        {"id": ids[0], "title": "buy groceries + flowers"},
    )
    assert edit.decision is Decision.ALLOW
    policy_outcome(
        edit,
        rationale=(
            "MODIFY_FS with allows_destructive on the cap bypasses the "
            "destructive-op gate; reversible/system bypasses the "
            "reversibility gate. Both gates needed to be open."
        ),
    )
    tool("tasks.edit → ok")

    step(4, "Complete a task")
    ai(f"call tasks.complete(id={ids[1][:6]}…)")
    done = await app.tool_client.call_tool(s.id, "tasks.complete", {"id": ids[1]})
    assert done.decision is Decision.ALLOW
    policy_outcome(done)
    tool("tasks.complete → ok; task marked done in store.")

    step(5, "Try to delete a task — refused")
    note(
        "DELETE_FS routes through both gates: the destructive-op gate "
        "(cap has no allows_destructive) AND the reversibility gate "
        "(tasks.delete declares irreversible/external). Either alone "
        "would block; in concert they double up."
    )
    ai(f"call tasks.delete(id={ids[2][:6]}…)")
    deleted = await app.tool_client.call_tool(s.id, "tasks.delete", {"id": ids[2]})
    policy_outcome(
        deleted,
        rationale=(
            "Refused. The operator would either widen the cap with "
            "allows_destructive=True for the delete kind OR mint a "
            "single-use override grant for this specific deletion."
        ),
    )
    tool("(skipped — task remains in the store)")

    step(6, "Final list reflects edit + complete; delete did not run")
    final = await app.tool_client.call_tool(s.id, "tasks.list", {"include_done": True})
    final_titles = [t["title"] for t in final.output["tasks"]]
    final_done = [t["done"] for t in final.output["tasks"]]
    audit(f"final titles: {final_titles}")
    audit(f"  done flags: {final_done}")
    assert "buy groceries + flowers" in final_titles  # edit landed
    assert True in final_done  # complete landed
    assert "call dentist" in final_titles  # delete refused, task survives
