"""Expense categorization — receipts → report → submit.

Workflow: the operator stages 3 receipt PDFs on disk. The agent
reads each via REAL fs.read_pdf, an inspector raises CONFIDENTIAL_
FINANCIAL when dollar amounts are detected, tasks are added per
category, a consolidated report is written, and a draft email goes
to the accountant. The accountant's address is bound by a Pattern ③
handle so the planner cannot redirect to an attacker. The Brewer-
Nash financial-meets-email rule refuses the naive send; an override
clears it for one dispatch.
"""

from __future__ import annotations

import io
import re
from typing import Any

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from capabledeputy.daemon.override_handlers import make_override_handlers
from capabledeputy.patterns.reference_handle import (
    ReferenceHandleStore,
    ResolvedLabels,
)
from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.policy.labels import (
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
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
from capabledeputy.substrate.inspector_port import InspectorRaiseResult, RaiseOnlyInspector
from capabledeputy.policy.context import PolicyContext
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolResult
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


def _make_receipt_pdf(vendor: str, amount: float) -> bytes:
    text = f"{vendor} - Amount: ${amount:.2f}"
    w = PdfWriter()
    p = w.add_blank_page(width=612, height=792)
    content = DecodedStreamObject()
    content.set_data(f"BT /F1 12 Tf 72 700 Td ({text}) Tj ET".encode("latin-1"))
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
    p[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})},
    )
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


class _FinancialContentDetector(RaiseOnlyInspector):
    """Schema-bounded financial detection. Approximates Pattern ②
    DUAL_LLM: if a returned value contains a dollar-amount pattern,
    raise untrusted provenance on the session (provenance bumped toward
    EXTERNAL_UNTRUSTED) — keeping the planner from naively forwarding
    the raw amount."""

    _AMOUNT_RE = re.compile(r"\$\d+(?:\.\d{2})?")

    def inspect(
        self,
        *,
        value: object,
        current_label_state: LabelState,
    ) -> InspectorRaiseResult:
        if not self._AMOUNT_RE.search(str(value)):
            return InspectorRaiseResult()
        # Raising EXTERNAL_UNTRUSTED provenance via Axis B.
        return InspectorRaiseResult(
            raise_state=LabelState(
                b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)}),
            ),
        )


async def _accountant_email_post(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    """Handle-aware accountant tool. The recipient is bound at dispatch
    time so the planner can't redirect to an attacker."""
    return ToolResult(output={"sent": True, "to": args.get("to_handle")})


def _make_accountant_tool() -> ToolDefinition:
    return ToolDefinition(
        name="email.send_to_accountant",
        description="Send a report to the bound accountant address.",
        capability_kind=CapabilityKind.SEND_EMAIL,
        handler=_accountant_email_post,
        target_arg="to_handle",
        accepts_handles=True,
        handle_arg_names=("to_handle",),
        parameters_schema={
            "type": "object",
            "properties": {
                "to_handle": {"type": "string", "description": "Bound accountant handle UUID"},
                "body": {"type": "string", "description": "Email body"},
            },
            "required": ["to_handle", "body"],
        },
        operations=(Operation(EffectClass.COMMUNICATE),),
        risk_ids=("RISK-DATA-EXFIL-AGENT-TOOLS",),
        default_reversibility={"degree": "irreversible", "agent": "external"},
        social_commitment=True,
        surfaces_destination_id=True,
    )


@pytest.mark.asyncio
async def test_expense_categorization_demo(tmp_path: Any) -> None:
    demo_header(
        "Expense Categorization — receipts → report → submit",
        blurb=(
            "Three real receipt PDFs (generated via pypdf) → real "
            "fs.read_pdf → inspector flags financial content → tasks "
            "track each → fs.create consolidated report → Pattern ③ "
            "handle for accountant → send refused → override → sent."
        ),
        models=(
            "Brewer-Nash financial-meets-email",
            "FR-047 unforgeable handles",
            "FR-019 social-commitment",
            "FR-038 override origin",
        ),
        patterns=(
            "Pattern ② DUAL_LLM-style inspector bracket",
            "Pattern ③ ReferenceHandle binding",
        ),
    )

    # Stage 3 receipt PDFs on disk.
    receipts_dir = tmp_path / "receipts"
    receipts_dir.mkdir()
    receipt_files = []
    for vendor, amount in (("Office Depot", 89.50), ("Uber", 23.40), ("Hotel CA", 412.00)):
        p = receipts_dir / f"{vendor.lower().replace(' ', '_')}.pdf"
        p.write_bytes(_make_receipt_pdf(vendor, amount))
        receipt_files.append((vendor, amount, p))
    report_path = tmp_path / "expense-report.md"

    inspector = _FinancialContentDetector()
    handle_store = ReferenceHandleStore()
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
        inspectors=(inspector,),
        handle_store=handle_store,
        override_policies=override_policies,
        override_grants=override_grants,
    )
    app = make_app(tmp_path, policy_context=ctx)
    app.registry.register(_make_accountant_tool())
    await app.startup()

    s = await make_session(
        app,
        axis_a_categories=(("finance", Tier.SENSITIVE),),
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
                    kind=CapabilityKind.MODIFY_FS,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                    allows_destructive=True,
                ),
                Capability(
                    kind=CapabilityKind.SEND_EMAIL,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
            },
        ),
    )

    step(1, "Read 3 receipt PDFs (real fs.read_pdf)")
    user('"categorize my receipts from this trip"')
    pdf_outcomes = []
    for vendor, _amount, path in receipt_files:
        ai(f'call fs.read_pdf(path="{path.name}")')
        out = await app.tool_client.call_tool(s.id, "fs.read_pdf", {"path": str(path)})
        assert out.decision is Decision.ALLOW
        assert out.output["ok"]
        pdf_outcomes.append(out)
        tool(f"fs.read_pdf({vendor}) → '{out.output['text'].strip()}'")
    note("Inspector ran on each output — financial markers detected.")

    # The inspector raised EXTERNAL_UNTRUSTED provenance on financial
    # content. The Brewer-Nash rule uses the session's label_state to
    # decide on financial-meets-email constraints.

    step(2, "Track each receipt as a task")
    for vendor, amount, _ in receipt_files:
        ai(f'call tasks.add(title="{vendor} ${amount:.2f}")')
        t_out = await app.tool_client.call_tool(
            s.id,
            "tasks.add",
            {"title": f"{vendor} ${amount:.2f}"},
        )
        assert t_out.decision is Decision.ALLOW
    tool(f"tasks.add x {len(receipt_files)} → ok")

    step(3, "Mark the smallest receipt complete (already reimbursed)")
    listed = await app.tool_client.call_tool(s.id, "tasks.list", {})
    smallest_id = next(t["id"] for t in listed.output["tasks"] if "Uber" in t["title"])
    ai(f"call tasks.complete(id={smallest_id[:6]}…)")
    completed = await app.tool_client.call_tool(
        s.id,
        "tasks.complete",
        {"id": smallest_id},
    )
    # tasks.complete is MODIFY_FS without allows_destructive on this
    # session's cap → REQUIRE_APPROVAL. Honest outcome.
    policy_outcome(completed)
    tool("(would land via the operator's approval queue)")

    step(4, "Compose consolidated expense report on disk")
    total = sum(a for _, a, _ in receipt_files)
    ai(f'call fs.create(path="{report_path.name}", content="…")')
    report_create = await app.tool_client.call_tool(
        s.id,
        "fs.create",
        {
            "path": str(report_path),
            "content": (
                "# Expense Report 2026-05-20\n\n"
                + "\n".join(f"- {v}: ${a:.2f}" for v, a, _ in receipt_files)
                + f"\n\n**Total: ${total:.2f}**\n"
            ),
        },
    )
    assert report_create.decision is Decision.ALLOW
    assert report_create.output["ok"]
    policy_outcome(report_create)
    tool(f"fs.create → wrote {report_create.output['bytes_written']} bytes.")

    step(5, "Bind the accountant's address via Pattern ③ handle")
    user('"set accountant address = accountant@firm.example"')
    accountant_handle = handle_store.issue(
        s.id,
        value="accountant@firm.example",
        labels=ResolvedLabels(axis_a=("finance",), axis_b=("principal-direct",)),
    )
    audit(
        f"handle id={accountant_handle.id} — planner sees the UUID, the "
        "dispatcher binds the address."
    )

    step(6, "Send report to accountant via the bound handle — REFUSED")
    note(
        "Even with a bound recipient, the session is now "
        "CONFIDENTIAL_FINANCIAL (we wrote the inspector-recognized "
        "amounts to it via memory). Brewer-Nash financial-meets-email "
        "refuses the egress."
    )
    ai(f"call email.send_to_accountant(to_handle={str(accountant_handle.id)[:8]}…)")
    refused = await app.tool_client.call_tool(
        s.id,
        "email.send_to_accountant",
        {"to_handle": str(accountant_handle.id), "body": "Report attached."},
    )
    assert refused.decision is Decision.DENY
    policy_outcome(refused)

    step(7, "Override with security-officer attestation")
    user("override.request  →  SEND_EMAIL")
    handlers = make_override_handlers(override_grants, override_policies)
    req = await handlers["override.request"](
        {
            "session_id": str(s.id),
            "action_kind": "SEND_EMAIL",
            "target": str(accountant_handle.id),
            "floor": "max-tier-clearance",
            "invoker": "alice",
            "category": "finance",
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

    ai(f"call email.send_to_accountant(to_handle={str(accountant_handle.id)[:8]}…) — retry")
    sent = await app.tool_client.call_tool(
        s.id,
        "email.send_to_accountant",
        {"to_handle": str(accountant_handle.id), "body": "Report attached."},
    )
    assert sent.decision is Decision.ALLOW
    assert sent.rule == "override-grant-active"
    policy_outcome(sent)
    tool(f"email.send_to_accountant → ok; recipient bound = {sent.output['to']}")
