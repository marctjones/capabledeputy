from uuid import uuid4

from capabledeputy.daemon.policy_handlers import make_policy_handlers
from capabledeputy.policy.labels import tags_for_labels_strings


async def test_show_returns_labels_kinds_and_rules() -> None:
    handlers = make_policy_handlers()
    result = await handlers["policy.show"]({})
    # policy.show no longer returns labels; they're accessed through tags_for_labels_strings
    assert "SEND_EMAIL" in result["capability_kinds"]


async def test_test_with_no_capability_denies() -> None:
    handlers = make_policy_handlers()
    result = await handlers["policy.test"](
        {
            "action_kind": "SEND_EMAIL",
            "target": "alice@example.com",
            "labels": [],
            "capabilities": [],
        },
    )
    assert result["decision"] == "deny"
    assert "no matching capability" in result["reason"]


async def test_test_with_capability_allows_when_no_conflict() -> None:
    handlers = make_policy_handlers()
    cap = {
        "kind": "SEND_EMAIL",
        "pattern": "*@example.com",
        "expiry": "session",
        "origin": "system_default",
        "audit_id": str(uuid4()),
    }
    result = await handlers["policy.test"](
        {
            "action_kind": "SEND_EMAIL",
            "target": "alice@example.com",
            "labels": [],
            "capabilities": [cap],
        },
    )
    assert result["decision"] == "allow"
    assert result["matched_capability"]["kind"] == "SEND_EMAIL"


async def test_test_health_blocks_email() -> None:
    handlers = make_policy_handlers()
    cap = {
        "kind": "SEND_EMAIL",
        "pattern": "*",
        "expiry": "session",
        "origin": "system_default",
        "audit_id": str(uuid4()),
    }
    label_state = tags_for_labels_strings(frozenset({"confidential.health"}))
    result = await handlers["policy.test"](
        {
            "action_kind": "SEND_EMAIL",
            "target": "alice@example.com",
            "labels": label_state.to_dict(),
            "capabilities": [cap],
        },
    )
    assert result["decision"] == "deny"
    assert result["rule"] == "health-meets-egress"


async def test_test_financial_purchase_requires_approval() -> None:
    handlers = make_policy_handlers()
    cap = {
        "kind": "QUEUE_PURCHASE",
        "pattern": "*",
        "expiry": "session",
        "origin": "system_default",
        "audit_id": str(uuid4()),
        "max_amount": 1000,
    }
    label_state = tags_for_labels_strings(frozenset({"confidential.financial"}))
    result = await handlers["policy.test"](
        {
            "action_kind": "QUEUE_PURCHASE",
            "target": "amazon",
            "amount": 50,
            "labels": label_state.to_dict(),
            "capabilities": [cap],
        },
    )
    assert result["decision"] == "require_approval"
    assert result["rule"] == "financial-meets-purchase"


async def test_validate_returns_valid_for_default_rules() -> None:
    handlers = make_policy_handlers()
    result = await handlers["policy.validate"]({})
    assert result["valid"] is True
    assert result["errors"] == []
