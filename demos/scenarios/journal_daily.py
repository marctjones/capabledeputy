"""Daily journal — fs.* + tasks.* + memory.delete with override.

Personal journaling workflow: yesterday's entry on disk, today's
entry created across multiple writes (FR-034 optimistic-auto fires
on each), revisions require approval, action items tracked in tasks
(add/edit/complete), an attempt to archive an old draft from memory
refuses (destructive-op + reversibility), and the override path
clears the archive.
"""

from __future__ import annotations

from typing import Any

import pytest

from capabledeputy.daemon.override_handlers import make_override_handlers
from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.overrides import (
    HardFloor,
    OverrideGrantStore,
    OverridePolicies,
    OverridePolicy,
    OverridePolicyEntry,
)
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier
from capabledeputy.policy.context import PolicyContext
from demos.scenarios._helpers import (
    ai,
    audit,
    demo_header,
    make_app,
    make_session,
    note,
    policy,
    policy_outcome,
    step,
    tool,
    user,
)


@pytest.mark.asyncio
async def test_journal_daily_demo(tmp_path: Any) -> None:
    demo_header(
        "Daily Journal — fs.* + tasks.* + memory.delete override",
        blurb=(
            "Read yesterday → create today (optimistic-auto on multiple "
            "writes) → revise (REQUIRE_APPROVAL) → track action items via "
            "tasks (add/edit/complete) → archive old draft via memory."
            "delete (refused) → override clears."
        ),
        models=(
            "FR-034 optimistic-auto",
            "FR-019 reversibility on fs.modify",
            "destructive-op gate on DELETE_FS",
            "FR-038 override origin",
        ),
        patterns=(
            "optimistic burn on local writes",
            "standing-cap for low-risk MODIFY_FS",
        ),
    )

    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    yesterday_path = journal_dir / "2026-05-19.md"
    yesterday_path.write_text(
        "# 2026-05-19\n\nWorked on demo refactor. Pending: write spec 006.\n",
        encoding="utf-8",
    )
    today_path = journal_dir / "2026-05-20.md"

    override_policies = OverridePolicies(
        by_floor={
            HardFloor.MAX_TIER_CLEARANCE: OverridePolicyEntry(
                floor=HardFloor.MAX_TIER_CLEARANCE,
                policy=OverridePolicy.DUAL_CONTROL,
                authorized_principal_ids=frozenset({"alice"}),
                attester_principal_ids=frozenset({"security-officer"}),
                expiry_seconds=300,
            ),
        },
    )
    override_grants = OverrideGrantStore()
    ctx = PolicyContext(
        override_policies=override_policies,
        override_grants=override_grants,
    )
    app = make_app(tmp_path, policy_context=ctx)
    await app.startup()

    # Pre-seed memory with an old draft to archive later.
    app.memory.write("draft-2026-04", "Old reflections, archive me.", frozenset())

    s = await make_session(
        app,
        axis_a_categories=(("personal", Tier.SENSITIVE),),
        capabilities=frozenset(
            {
                Capability(
                    kind=CapabilityKind.READ_FS,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
                Capability(
                    kind=CapabilityKind.CREATE_FS,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
                # MODIFY_FS WITHOUT allows_destructive — modify on disk
                # routes through approval naturally.
                Capability(
                    kind=CapabilityKind.MODIFY_FS,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                    allows_destructive=True,
                ),
                # DELETE_FS WITHOUT allows_destructive — delete refuses.
                Capability(
                    kind=CapabilityKind.DELETE_FS,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
            },
        ),
    )

    step(1, "Read yesterday's entry")
    ai(f'call fs.read(path="{yesterday_path}")')
    read = await app.tool_client.call_tool(s.id, "fs.read", {"path": str(yesterday_path)})
    assert read.decision is Decision.ALLOW
    policy_outcome(read)
    tool(f"fs.read → ok ({len(read.output['text'])} chars).")

    step(2, "Create today's entry — optimistic-auto on a clean session")
    ai(f'call fs.create(path="{today_path}", content="# 2026-05-20…")')
    created = await app.tool_client.call_tool(
        s.id,
        "fs.create",
        {
            "path": str(today_path),
            "content": "# 2026-05-20\n\n## Morning\n- Coffee, plan day.\n",
        },
    )
    assert created.decision is Decision.ALLOW
    policy_outcome(created)
    tool(f"fs.create → wrote {created.output['bytes_written']} bytes.")

    step(3, "Track action items via tasks (add x 3)")
    user('"add tasks: spec, demos, lint"')
    task_ids = []
    for title in ("finalize spec 006", "wrap demo refactor", "lint sweep"):
        ai(f'call tasks.add(title="{title}")')
        out = await app.tool_client.call_tool(s.id, "tasks.add", {"title": title})
        assert out.decision is Decision.ALLOW
        task_ids.append(out.output["id"])
    tool(f"tasks.add x 3 → ids={[t[:6] for t in task_ids]}")

    step(4, "Rename one task (tasks.edit)")
    ai(f"call tasks.edit(id={task_ids[0][:6]}…, title='ship spec 006')")
    edited = await app.tool_client.call_tool(
        s.id,
        "tasks.edit",
        {"id": task_ids[0], "title": "ship spec 006"},
    )
    assert edited.decision is Decision.ALLOW
    policy_outcome(edited)
    tool("tasks.edit → ok")

    step(5, "Complete an item (tasks.complete)")
    ai(f"call tasks.complete(id={task_ids[2][:6]}…)")
    completed = await app.tool_client.call_tool(
        s.id,
        "tasks.complete",
        {"id": task_ids[2]},
    )
    assert completed.decision is Decision.ALLOW
    policy_outcome(completed)
    tool("tasks.complete → ok")

    step(6, "Revise today's entry (fs.modify) — REQUIRE_APPROVAL")
    note(
        "Even with allows_destructive on the cap, fs.modify declares "
        "reversible-with-friction/human so the reversibility gate "
        "forces approval. The operator confirms via the queue."
    )
    ai(f'call fs.modify(path="{today_path}", content="…revised…")')
    modify = await app.tool_client.call_tool(
        s.id,
        "fs.modify",
        {
            "path": str(today_path),
            "content": (
                "# 2026-05-20\n\n## Morning\n- Coffee, plan day.\n\n"
                "## Evening\n- Reflected on the day.\n"
            ),
        },
    )
    policy_outcome(modify)
    tool("(deferred — operator approves via queue)")

    step(7, "Archive old draft (memory.delete) — REFUSED")
    note(
        "memory.delete is irreversible/external — the reversibility "
        "gate refuses regardless of cap. The destructive-op gate "
        "ALSO fires (no allows_destructive). Double refusal."
    )
    ai('call memory.delete(key="draft-2026-04")')
    deleted = await app.tool_client.call_tool(
        s.id,
        "memory.delete",
        {"key": "draft-2026-04"},
    )
    assert deleted.decision is Decision.DENY
    policy_outcome(deleted)

    step(8, "Override the archive deletion")
    user("override.request  →  DELETE_FS  draft-2026-04")
    handlers = make_override_handlers(override_grants, override_policies)
    req = await handlers["override.request"](
        {
            "session_id": str(s.id),
            "action_kind": "DELETE_FS",
            "target": "draft-2026-04",
            "floor": "max-tier-clearance",
            "invoker": "alice",
            "category": "personal",
            "tier": "sensitive",
            "friction_confirmed": True,
        }
    )
    user("override.attest  --attester security-officer")
    await handlers["override.attest"](
        {
            "grant_id": req["id"],
            "attester": "security-officer",
            "confirmed": True,
        }
    )
    policy("active", rule="FR-036", rationale="distinct attester ok.")

    ai('call memory.delete(key="draft-2026-04") — retry')
    final = await app.tool_client.call_tool(
        s.id,
        "memory.delete",
        {"key": "draft-2026-04"},
    )
    assert final.decision is Decision.ALLOW
    assert final.rule == "override-grant-active"
    policy_outcome(final)
    tool("memory.delete → ok; old draft archived; grant CONSUMED.")
    audit("Action items survive; old draft removed; today's entry on disk.")
    assert today_path.is_file()
    assert app.memory.read("draft-2026-04") is None
