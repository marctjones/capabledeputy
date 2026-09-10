from uuid import uuid4

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.purchase_reversibility import parse_purchase_reversibility_policy
from capabledeputy.tools.native.purchase import PurchaseQueue, make_purchase_tools
from capabledeputy.tools.registry import ToolContext


def _ctx() -> ToolContext:
    from capabledeputy.policy.labels import LabelState

    return ToolContext(session_id=uuid4(), label_state=LabelState())


async def test_purchase_queue_records_and_returns_message() -> None:
    queue = PurchaseQueue()
    tools = {t.name: t for t in make_purchase_tools(queue)}

    result = await tools["purchase.queue"].handler(
        {"vendor": "amazon", "item": "book", "amount": 25},
        _ctx(),
    )
    assert result.output["queued"] is True
    assert result.output["vendor"] == "amazon"
    assert result.output["amount"] == 25
    assert "approval" in result.output["message"]


async def test_purchase_queue_records_session_id() -> None:
    from capabledeputy.policy.labels import LabelState

    queue = PurchaseQueue()
    tools = {t.name: t for t in make_purchase_tools(queue)}

    sid = uuid4()
    ctx = ToolContext(session_id=sid, label_state=LabelState())
    await tools["purchase.queue"].handler(
        {"vendor": "amazon", "item": "x"},
        ctx,
    )
    assert len(queue.all()) == 1
    assert queue.all()[0].session_id == sid


async def test_purchase_queue_handles_missing_amount() -> None:
    queue = PurchaseQueue()
    tools = {t.name: t for t in make_purchase_tools(queue)}

    result = await tools["purchase.queue"].handler(
        {"vendor": "amazon", "item": "free"},
        _ctx(),
    )
    assert result.output["amount"] is None


def test_tool_metadata() -> None:
    tools = make_purchase_tools(PurchaseQueue())
    assert tools[0].name == "purchase.queue"
    assert tools[0].capability_kind == CapabilityKind.QUEUE_PURCHASE
    assert tools[0].amount_arg == "amount"
    assert tools[0].target_arg == "vendor"
    assert tools[0].default_reversibility == {"degree": "irreversible", "agent": "external"}


def test_no_reversibility_policy_resolver_always_declines() -> None:
    """No policy wired (the default) ⇒ the resolver always returns
    None, so `default_reversibility` (irreversible/external) is the
    only thing engine.decide() ever sees -- today's behavior,
    unchanged."""
    tools = make_purchase_tools(PurchaseQueue())
    resolver = tools[0].reversibility_resolver
    assert resolver is not None
    assert resolver({"vendor": "amazon", "item": "x", "amount": 10}) is None


def test_reversibility_policy_resolver_overrides_for_matching_vendor() -> None:
    policy = parse_purchase_reversibility_policy(
        {
            "purchase_reversibility": [
                {
                    "match": {"vendor_glob": "amazon*"},
                    "reversibility": {"degree": "reversible", "agent": "system"},
                },
            ],
        },
    )
    tools = make_purchase_tools(PurchaseQueue(), reversibility_policy=policy)
    resolver = tools[0].reversibility_resolver
    assert resolver is not None
    assert resolver({"vendor": "amazon-fresh", "item": "x", "amount": 10}) == {
        "degree": "reversible",
        "agent": "system",
    }
    # A non-matching vendor still declines -- the static floor holds.
    assert resolver({"vendor": "ticketmaster", "item": "x", "amount": 10}) is None
