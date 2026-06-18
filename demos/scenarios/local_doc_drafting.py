"""Local document drafting — fs.read → compose → fs.create → fs.modify.

Workflow: the operator points the assistant at a real meeting-notes
text file. The agent reads it, composes a markdown summary, writes
the summary to a NEW file via fs.create, then revises the file via
fs.modify. Finally it tries to email the summary file's contents to
a teammate — refused (untrusted-meets-egress, because reading the
source tainted the session).

This is the canonical "summarize a local document into a saved
markdown note" personal-assistant workflow. Uses REAL fs.read +
fs.create + fs.modify against actual files on disk in tmp_path.
"""

from __future__ import annotations

from typing import Any

import pytest

from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
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
    policy_outcome,
    step,
    tool,
    user,
)


@pytest.mark.asyncio
async def test_local_doc_drafting_demo(tmp_path: Any) -> None:
    demo_header(
        "Local Doc Drafting — read → fs.create → fs.modify → egress refused",
        blurb=(
            "Read notes, compose a markdown summary, write it to disk, "
            "revise it, attempt to email — refused. The whole pipeline "
            "uses REAL fs.* tools against real files on disk."
        ),
        models=(
            "Brewer-Nash untrusted-meets-egress",
            "destructive-op gate on MODIFY_FS",
        ),
        patterns=("local file write with operator-supplied content",),
    )

    # Stage the source document on disk.
    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "raw-notes.txt"
    source.write_text(
        "Raw meeting notes 2026-05-20\n"
        "- Discussed Q3 roadmap\n"
        "- Action: Anna to circulate slides by Friday\n",
        encoding="utf-8",
    )
    summary = docs / "summary.md"

    ctx = PolicyContext()
    app = make_app(tmp_path, policy_context=ctx)
    await app.startup()
    s = await make_session(
        app,
        axis_a_categories=(("documents", Tier.SENSITIVE),),
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
                # MODIFY_FS WITHOUT allows_destructive — so the modify
                # step routes through REQUIRE_APPROVAL (intentional).
                # The operator pre-grants destructive scope on this
                # path explicitly in the next cap.
                Capability(
                    kind=CapabilityKind.MODIFY_FS,
                    pattern=str(summary),
                    origin=CapabilityOrigin.USER_APPROVED,
                    allows_destructive=True,
                ),
                Capability(
                    kind=CapabilityKind.SEND_EMAIL,
                    pattern="*@example.com",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
            },
        ),
    )

    step(1, "Read the source notes")
    user('"summarize my raw-notes.txt"')
    ai(f'call fs.read(path="{source}")')
    read = await app.tool_client.call_tool(s.id, "fs.read", {"path": str(source)})
    assert read.decision is Decision.ALLOW
    assert read.output["ok"]
    policy_outcome(read)
    tool(f"fs.read → ok ({len(read.output['text'])} chars); UNTRUSTED_USER_INPUT propagated.")

    step(2, "Create a new markdown summary file (fs.create)")
    note("CREATE_FS path is non-destructive; refuses if the target exists.")
    ai(f'call fs.create(path="{summary}", content="# Summary\\n...")')
    create_outcome = await app.tool_client.call_tool(
        s.id,
        "fs.create",
        {
            "path": str(summary),
            "content": (
                "# Meeting summary 2026-05-20\n\nDiscussed Q3 roadmap; Anna to circulate slides.\n"
            ),
        },
    )
    policy_outcome(create_outcome)
    if create_outcome.decision is Decision.ALLOW and create_outcome.output["ok"]:
        tool(f"fs.create → wrote {create_outcome.output['bytes_written']} bytes")
    else:
        tool(f"(deferred: {create_outcome.output.get('error', '—')})")

    step(3, "Revise the summary file (fs.modify)")
    note(
        "fs.modify is destructive (MODIFY_FS) AND declared reversible-"
        "with-friction/human. Even with allows_destructive on the cap, "
        "the reversibility gate forces REQUIRE_APPROVAL. The point: a "
        "modify-existing-file action is genuinely higher-stakes than "
        "creating a new one — the tool's own declaration enforces that "
        "asymmetry, separate from the capability."
    )
    ai(f'call fs.modify(path="{summary}", content="… revised …")')
    modify_outcome = await app.tool_client.call_tool(
        s.id,
        "fs.modify",
        {
            "path": str(summary),
            "content": (
                "# Meeting summary 2026-05-20 (rev 2)\n\n"
                "- Q3 roadmap discussed.\n"
                "- Anna: circulate slides by Friday.\n"
            ),
        },
    )
    policy_outcome(modify_outcome)
    if modify_outcome.decision is Decision.ALLOW and (modify_outcome.output or {}).get("ok"):
        tool(f"fs.modify → wrote {modify_outcome.output['bytes_written']} bytes")
    else:
        tool("(deferred — operator would approve via the queued request)")

    step(4, "Try to email the summary contents")
    note(
        "Reading the source tainted the session UNTRUSTED_USER_INPUT. The "
        "session-level taint persists even though the summary file was "
        "freshly composed — Brewer-Nash refuses the egress."
    )
    ai('call email.send(to="bob@example.com", body="…summary content…")')
    sent = await app.tool_client.call_tool(
        s.id,
        "email.send",
        {
            "to": "bob@example.com",
            "subject": "Meeting summary",
            "body": "Anna will circulate slides by Friday.",
        },
    )
    assert sent.decision is Decision.DENY
    policy_outcome(
        sent,
        rationale=(
            "Brewer-Nash untrusted-meets-egress. The legitimate path is "
            "to compose the send from a fresh session that never read "
            "the raw notes — or to summarize through Pattern ②."
        ),
    )
    tool("(skipped)")

    # Confirm the original summary file exists on disk (fs.create
    # succeeded). The fs.modify was REQUIRE_APPROVAL by design — we
    # do NOT assert rev2 content, because the modify didn't dispatch.
    assert summary.is_file()
    audit(f"on-disk: {summary} ({summary.stat().st_size} bytes; rev1 contents)")
