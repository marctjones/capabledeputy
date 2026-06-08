"""Local-document Q&A — fs.read + fs.read_pdf with label propagation.

Workflow: the operator points the assistant at a local text file and
a local PDF. The agent reads both (UNTRUSTED_USER_INPUT taint enters
the session — operator-curated, but not vetted). It can save notes
to local memory freely. The moment it tries to forward the content
via email, Brewer-Nash refuses — untrusted-meets-egress.

This demo uses REAL native tools (`fs.read` and `fs.read_pdf` from
`src/capabledeputy/tools/native/fs.py`) against REAL local files in
the test's tmp_path. No external services; no stubs for the read path.

The PDF is generated at test-time via pypdf so the demo is fully
hermetic — no fixture file needed.
"""

from __future__ import annotations

import io
from typing import Any

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier
from capabledeputy.tools.client import PolicyContext
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


def _make_test_pdf(text: str) -> bytes:
    """Generate a single-page PDF containing `text`. Used to give the
    demo a real PDF without relying on a checked-in fixture."""
    w = PdfWriter()
    p = w.add_blank_page(width=612, height=792)
    content = DecodedStreamObject()
    content.set_data(
        f"BT /F1 12 Tf 72 700 Td ({text}) Tj ET".encode("latin-1"),
    )
    content_ref = w._add_object(content)
    p[NameObject("/Contents")] = content_ref
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        },
    )
    font_ref = w._add_object(font)
    resources = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})},
    )
    p[NameObject("/Resources")] = resources
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_local_doc_qa_demo(tmp_path: Any) -> None:
    demo_header(
        "Local Doc Q&A — real fs.read + fs.read_pdf",
        blurb=(
            "Operator stages a text note and a PDF report on disk. The "
            "agent reads both (REAL fs.* tools, not stubs). Notes can "
            "be saved locally; forwarding via email refuses (untrusted-"
            "meets-egress)."
        ),
        models=("Brewer-Nash untrusted-meets-egress", "label propagation on fs.read"),
        patterns=("local file tool with operator-curated label",),
    )

    # Stage real fixture files under the test's tmp_path.
    docs = tmp_path / "docs"
    docs.mkdir()
    note_path = docs / "meeting-notes.txt"
    note_path.write_text(
        "Quarterly review meeting 2026-05-20:\n"
        "- Revenue up 12%.\n"
        "- Next-quarter focus: enterprise sales.\n",
        encoding="utf-8",
    )
    pdf_path = docs / "q1-report.pdf"
    pdf_path.write_bytes(_make_test_pdf("Q1 Report: profit 12 percent. Stable outlook."))

    # Sanity check the PDF is parseable so the demo's value is real.
    PdfReader(str(pdf_path))

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
                Capability(
                    kind=CapabilityKind.SEND_EMAIL,
                    pattern="*@example.com",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
            },
        ),
    )

    step(1, "Agent reads the text note (real file)")
    user('"summarize my meeting-notes.txt"')
    ai(f'call fs.read(path="{note_path}")')
    txt_out = await app.tool_client.call_tool(s.id, "fs.read", {"path": str(note_path)})
    assert txt_out.decision is Decision.ALLOW
    assert txt_out.output["ok"]
    policy_outcome(txt_out)
    excerpt = txt_out.output["text"][:60].replace("\n", " ")
    tool(f'fs.read → ok; first 60 chars: "{excerpt}…"')

    step(2, "Agent reads the PDF report (real PDF)")
    user('"and what does the q1 report say?"')
    ai(f'call fs.read_pdf(path="{pdf_path}")')
    pdf_out = await app.tool_client.call_tool(s.id, "fs.read_pdf", {"path": str(pdf_path)})
    assert pdf_out.decision is Decision.ALLOW
    assert pdf_out.output["ok"]
    policy_outcome(pdf_out)
    tool(
        f"fs.read_pdf → ok; {pdf_out.output['n_pages']} page(s); "
        f"text: {pdf_out.output['text'].strip()!r}"
    )

    s_after = app.graph.get(s.id)
    ls = s_after.label_state
    labels = sorted({t.category for t in ls.a} | {t.level.value for t in ls.b})
    audit(f"session labels after both reads: {labels}")

    step(3, "Save a local note — allowed, non-egressing")
    ai('call memory.create(key="q1-summary", value=…)')
    save = await app.tool_client.call_tool(
        s.id,
        "memory.create",
        {"key": "q1-summary", "value": "Revenue up 12%; profit 12%."},
    )
    assert save.decision is Decision.ALLOW
    policy_outcome(
        save,
        rationale="Non-egressing data.create_local; carve-out applies.",
    )
    tool("memory.create → ok")

    step(4, "Try to email the summary to a teammate — refused")
    note(
        "Local fs reads tag the session UNTRUSTED_USER_INPUT (operator-"
        "curated but not vetted). The Brewer-Nash untrusted-meets-"
        "egress rule blocks the social.send_email."
    )
    ai('call email.send(to="bob@example.com", body="…q1 summary…")')
    sent = await app.tool_client.call_tool(
        s.id,
        "email.send",
        {
            "to": "bob@example.com",
            "subject": "Q1 summary",
            "body": "Revenue up 12%, profit 12%.",
        },
    )
    assert sent.decision is Decision.DENY
    policy_outcome(
        sent,
        rationale=(
            "Brewer-Nash refuses. The legitimate path is Pattern ② via "
            "quarantined.extract (returns a schema-bounded summary that "
            "is declassified by construction) OR a fresh session that "
            "composes the email without re-reading the source."
        ),
    )
    tool("(skipped)")


@pytest.mark.asyncio
async def test_fs_read_refuses_relative_paths(tmp_path: Any) -> None:
    """Auxiliary structural test: fs.read refuses relative paths so the
    daemon's cwd assumption can't be weaponized."""
    ctx = PolicyContext()
    app = make_app(tmp_path, policy_context=ctx)
    await app.startup()
    s = await make_session(
        app,
        capabilities=frozenset(
            {
                Capability(
                    kind=CapabilityKind.READ_FS,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
            },
        ),
    )
    out = await app.tool_client.call_tool(
        s.id,
        "fs.read",
        {"path": "relative/path.txt"},
    )
    assert out.output["ok"] is False
    assert "relative" in out.output["error"]
