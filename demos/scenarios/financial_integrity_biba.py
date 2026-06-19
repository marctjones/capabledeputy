"""Financial integrity — Biba source trust for ledger updates.

Workflow: a user asks the assistant to update financial records from
two possible sources:

  - a direct bank integration, labeled SYSTEM_INTERNAL, which may update
    the ledger automatically;
  - an emailed "bank statement", labeled EXTERNAL_UNTRUSTED, which must
    not overwrite the ledger unless the human explicitly ratifies it.

The security model is Biba: low-integrity input cannot corrupt a higher-
integrity financial system. The human-ratified path is Clark-Wilson:
the operator performs an explicit transaction that turns the email into
an operator-verified update with an audit event.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from capabledeputy.audit.events import Event, EventType
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.context import PolicyContext
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.policy.labels import (
    CategoryTag,
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
)
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier
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


@dataclass
class Ledger:
    balances: dict[str, int]
    updates: list[dict[str, Any]]


def _financial_labels(provenance: ProvenanceLevel) -> LabelState:
    return LabelState(
        a=frozenset(
            {
                CategoryTag(
                    "financial",
                    Tier.REGULATED,
                    assignment_provenance="source-declared",
                ),
            },
        ),
        b=frozenset({ProvenanceTag(provenance)}),
    )


def _register_financial_tools(app: Any, ledger: Ledger) -> None:
    async def bank_statement_fetch(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        account_id = str(args["account_id"])
        balance = 18432
        return ToolResult(
            output={
                "account_id": account_id,
                "balance_cents": balance,
                "source": "direct-bank-api",
            },
            additional_tags=_financial_labels(ProvenanceLevel.SYSTEM_INTERNAL),
        )

    async def email_statement_read(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        return ToolResult(
            output={
                "message_id": str(args["message_id"]),
                "account_id": "ledger:checking",
                "balance_cents": 999999,
                "source": "email-attachment",
            },
            additional_tags=_financial_labels(ProvenanceLevel.EXTERNAL_UNTRUSTED),
        )

    async def ledger_update(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        account_id = str(args["account_id"])
        balance = int(args["balance_cents"])
        source = str(args.get("source", "unknown"))
        ledger.balances[account_id] = balance
        ledger.updates.append(
            {
                "account_id": account_id,
                "balance_cents": balance,
                "source": source,
            },
        )
        return ToolResult(output={"updated": True, "account_id": account_id})

    app.registry.register(
        ToolDefinition(
            name="bank.statement.fetch",
            description="Fetch a statement from the trusted direct bank integration.",
            capability_kind=CapabilityKind.WEB_FETCH,
            handler=bank_statement_fetch,
            target_arg="account_id",
            operations=(Operation(EffectClass.FETCH, subtype="bank.statement.fetch"),),
            risk_ids=("RISK-INDIRECT-INJECTION",),
            parameters_schema={
                "type": "object",
                "properties": {"account_id": {"type": "string"}},
                "required": ["account_id"],
            },
        ),
    )
    app.registry.register(
        ToolDefinition(
            name="email.statement.read",
            description="Read a statement attached to an email.",
            capability_kind=CapabilityKind.GMAIL_READ,
            handler=email_statement_read,
            target_arg="message_id",
            operations=(Operation(EffectClass.FETCH, subtype="email.statement.read"),),
            risk_ids=("RISK-INDIRECT-INJECTION",),
            parameters_schema={
                "type": "object",
                "properties": {"message_id": {"type": "string"}},
                "required": ["message_id"],
            },
        ),
    )
    app.registry.register(
        ToolDefinition(
            name="financial.ledger.update",
            description="Update the local financial ledger.",
            capability_kind=CapabilityKind.MODIFY_FS,
            handler=ledger_update,
            target_arg="account_id",
            operations=(
                Operation(
                    EffectClass.MUTATE_LOCAL,
                    subtype="financial.ledger.update",
                    required_floor=ProvenanceLevel.SYSTEM_INTERNAL,
                ),
            ),
            risk_ids=("RISK-DESTRUCTIVE-WRITE",),
            surfaces_destination_id=True,
            parameters_schema={
                "type": "object",
                "properties": {
                    "account_id": {"type": "string"},
                    "balance_cents": {"type": "integer"},
                    "source": {"type": "string"},
                },
                "required": ["account_id", "balance_cents"],
            },
        ),
    )


@pytest.mark.asyncio
async def test_financial_integrity_biba_demo(tmp_path: Any) -> None:
    demo_header(
        "Financial Integrity — Biba blocks emailed bank statements",
        blurb=(
            "A direct bank integration may update the ledger. An emailed bank "
            "statement is useful evidence, but its SMTP provenance is too low "
            "integrity to overwrite financial records unless the operator "
            "explicitly ratifies the update."
        ),
        models=("Biba integrity floor", "Clark-Wilson ratified transaction"),
        patterns=("source-trust floor → explicit human ratification",),
    )

    app = make_app(tmp_path, policy_context=PolicyContext())
    await app.startup()
    ledger = Ledger(balances={"ledger:checking": 10000}, updates=[])
    _register_financial_tools(app, ledger)

    caps = frozenset(
        {
            Capability(kind=CapabilityKind.WEB_FETCH, pattern="bank:*"),
            Capability(kind=CapabilityKind.GMAIL_READ, pattern="msg-*"),
            Capability(
                kind=CapabilityKind.MODIFY_FS,
                pattern="ledger:*",
                allows_destructive=True,
            ),
        },
    )

    step(1, "Trusted bank integration updates the ledger")
    trusted = await make_session(app, capabilities=caps)
    user("Sync checking balance from the bank integration.")
    ai('call bank.statement.fetch(account_id="bank:checking")')
    fetched = await app.tool_client.call_tool(
        trusted.id,
        "bank.statement.fetch",
        {"account_id": "bank:checking"},
    )
    assert fetched.decision is Decision.ALLOW
    policy_outcome(fetched)
    tool("bank.statement.fetch returned SYSTEM_INTERNAL financial data.")

    ai("call financial.ledger.update with the bank-provided balance")
    updated = await app.tool_client.call_tool(
        trusted.id,
        "financial.ledger.update",
        {
            "account_id": "ledger:checking",
            "balance_cents": fetched.output["balance_cents"],
            "source": "direct-bank-api",
        },
    )
    assert updated.decision is Decision.ALLOW
    assert ledger.balances["ledger:checking"] == 18432
    policy_outcome(updated)

    step(2, "Emailed statement tries to overwrite the ledger — denied")
    email_session = await make_session(app, capabilities=caps)
    user("Use the emailed bank statement to update my checking balance.")
    ai('call email.statement.read(message_id="msg-bank-statement")')
    emailed = await app.tool_client.call_tool(
        email_session.id,
        "email.statement.read",
        {"message_id": "msg-bank-statement"},
    )
    assert emailed.decision is Decision.ALLOW
    policy_outcome(emailed)
    tool("email.statement.read returned EXTERNAL_UNTRUSTED financial data.")

    ai("try financial.ledger.update from the emailed statement")
    blocked = await app.tool_client.call_tool(
        email_session.id,
        "financial.ledger.update",
        {
            "account_id": emailed.output["account_id"],
            "balance_cents": emailed.output["balance_cents"],
            "source": "email-attachment",
        },
    )
    assert blocked.decision is Decision.DENY
    assert blocked.rule == "integrity-floor-refused"
    assert ledger.balances["ledger:checking"] == 18432
    assert len(ledger.updates) == 1
    policy_outcome(
        blocked,
        rationale=(
            "Biba: SMTP-derived data is below the ledger update's "
            "SYSTEM_INTERNAL floor, so the handler is skipped."
        ),
    )

    step(3, "Operator ratifies the email-derived correction")
    note(
        "The system does not silently clean the email's provenance. The "
        "operator creates a new ratified transaction after verifying the "
        "statement out-of-band."
    )
    ratified = await make_session(app, capabilities=caps)
    await app.audit.write(
        Event(
            event_type=EventType.RATIFICATION_APPLIED,
            session_id=ratified.id,
            payload={
                "source": "email-attachment",
                "ratified_by": "principal:alice",
                "target": "ledger:checking",
                "reason": "operator verified statement before ledger update",
            },
        ),
    )
    audit("ratification.applied recorded operator verification.")
    ai("call financial.ledger.update with operator-ratified values")
    approved_update = await app.tool_client.call_tool(
        ratified.id,
        "financial.ledger.update",
        {
            "account_id": "ledger:checking",
            "balance_cents": emailed.output["balance_cents"],
            "source": "operator-ratified-email-statement",
        },
    )
    assert approved_update.decision is Decision.ALLOW
    assert ledger.balances["ledger:checking"] == 999999
    assert ledger.updates[-1]["source"] == "operator-ratified-email-statement"
    policy(
        "allow",
        rule="operator-ratified-update",
        rationale=(
            "The update is now principal-direct: a Clark-Wilson transaction "
            "with an explicit audit trail, not automatic trust in email."
        ),
    )

    events = await app.audit.read_all()
    assert any(e.event_type is EventType.RATIFICATION_APPLIED for e in events)
