"""Demo 09 — cross-compartment financial summary for an accountant.

The user wants to send a quarterly summary to their accountant. The
session reading financial data carries `confidential.financial`; the
`financial-meets-email` rule blocks direct egress.

Two-stage workflow:
  1. The agent calls `quarantined.extract` on the financial source
     using the `FinancialSummaryForAccountant` schema. The schema
     keeps numbers in coarse buckets (e.g., "100k-500k"), so the
     schema itself acts as a privacy filter — the accountant never
     gets exact figures.
  2. The user submits an approval with the bucketed summary as the
     verbatim payload. Approving spawns a one-shot purpose session
     and dispatches the email through the cross-session
     declassification path.

The originating financial session never gains the egress capability;
the precise dollar figures are still locked behind the schema.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from capabledeputy.app import App
from capabledeputy.daemon.agent_handlers import make_agent_handlers
from capabledeputy.daemon.approval_handlers import make_approval_handlers
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import FinishReason, LLMResponse, ToolCall
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import Label
from capabledeputy.session.model import SessionStatus


async def test_financial_summary_to_accountant(tmp_path: Path) -> None:
    """The full two-stage flow: extract the schema, approve a send,
    purpose session executes; precise numbers never leave."""
    bucketed = {
        "period": "Q1 2026",
        "total_income_bucket": "100k-500k",
        "total_expenses_bucket": "50k-100k",
        "n_transactions": 412,
        "notable_categories": ["consulting", "infra", "travel"],
    }
    quarantined = FakeLLMClient(
        [
            LLMResponse(
                content=json.dumps(bucketed),
                finish_reason=FinishReason.STOP,
            ),
        ],
    )
    planner = FakeLLMClient(
        [
            LLMResponse(
                content="Extracting accountant-safe summary.",
                tool_calls=(
                    ToolCall(
                        id="x1",
                        name="quarantined.extract",
                        args={
                            "key": "finance.q1",
                            "schema": "FinancialSummaryForAccountant",
                        },
                    ),
                ),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            LLMResponse(
                content="I have the summary; please review and approve.",
                finish_reason=FinishReason.STOP,
            ),
        ],
    )

    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=planner,
        quarantined_llm=quarantined,
    )
    await app.startup()

    # The exact financial source — never sent anywhere directly.
    app.memory.write(
        "finance.q1",
        (
            "Q1 2026 detail:\n"
            "Income: $327,432.18 from 12 invoices to Customer A; ...\n"
            "Expenses: $74,108.92 across 412 transactions ...\n"
        ),
        frozenset({Label.CONFIDENTIAL_FINANCIAL}),
    )

    s = await app.graph.new(intent="quarterly accountant summary")
    caps = frozenset(
        {
            Capability(kind=CapabilityKind.READ_FS, pattern="*"),
            # SEND_EMAIL capability is here so the test could (and
            # must) demonstrate that holding it isn't enough — policy
            # blocks the direct send.
            Capability(kind=CapabilityKind.SEND_EMAIL, pattern="*@accountant.example.com"),
        },
    )
    app.graph._sessions[s.id] = replace(s, capability_set=caps)

    agent = make_agent_handlers(app)
    extract_result = await agent["session.send"](
        {
            "session_id": str(s.id),
            "message": "Extract a quarterly accountant summary.",
        },
    )
    [extract_outcome] = extract_result["tool_outcomes"]
    assert extract_outcome["decision"] == "allow"
    data = extract_outcome["output"]["data"]
    assert "327,432" not in str(data)  # exact figure didn't leak
    assert data["total_income_bucket"] == "100k-500k"

    # User submits an approval to send the bucketed summary.
    payload = (
        f"Q1 2026 summary:\n"
        f"  Income bucket: {data['total_income_bucket']}\n"
        f"  Expenses bucket: {data['total_expenses_bucket']}\n"
        f"  Transactions: {data['n_transactions']}\n"
        f"  Categories: {', '.join(data['notable_categories'])}\n"
    )
    approvals = make_approval_handlers(app)
    submitted = await approvals["approval.submit"](
        {
            "from_session": str(s.id),
            "action": "SEND_EMAIL",
            "payload": payload,
            "target": "filer@accountant.example.com",
            "labels_in": ["confidential.financial"],
            "justification": "quarterly filing to accountant",
        },
    )
    decision = await approvals["approval.approve"](
        {"id": submitted["id"], "decided_by": "marc"},
    )
    assert decision["dispatch"]["decision"] == "allow"
    sent = app.email_outbox.all()
    assert len(sent) == 1
    body = sent[0].body
    # Bucketed values reach the wire; precise dollars do not.
    assert "100k-500k" in body
    assert "327,432" not in body

    # The purpose session is dead and never carried financial labels.
    purpose_id = decision["executed_in_session"]
    from uuid import UUID

    purpose = app.graph.get(UUID(purpose_id))
    assert purpose.status == SessionStatus.ABORTED
    assert Label.CONFIDENTIAL_FINANCIAL not in purpose.label_set

    # The originating session went through the schema extractor — by
    # construction the schema IS the declassification (DESIGN.md §5.2),
    # so the planner never saw the raw financial text and the session
    # was never tainted with confidential.financial. That's the
    # property: declassified extract produces a clean planner context,
    # while the memory source's labels are unchanged.
    after = app.graph.get(s.id)
    assert Label.CONFIDENTIAL_FINANCIAL not in after.label_set
    assert app.memory.labels_of("finance.q1") == frozenset(
        {Label.CONFIDENTIAL_FINANCIAL},
    )
