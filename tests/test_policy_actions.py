from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import CapabilityKind


def test_action_minimal() -> None:
    a = Action(kind=CapabilityKind.SEND_EMAIL, target="alice@example.com")
    assert a.kind == CapabilityKind.SEND_EMAIL
    assert a.target == "alice@example.com"
    assert a.amount is None


def test_action_with_amount() -> None:
    a = Action(kind=CapabilityKind.QUEUE_PURCHASE, target="amazon", amount=50)
    assert a.amount == 50


def test_action_is_frozen_and_hashable() -> None:
    a1 = Action(kind=CapabilityKind.READ_FS, target="/a")
    a2 = Action(kind=CapabilityKind.READ_FS, target="/a")
    assert a1 == a2
    assert hash(a1) == hash(a2)
