"""Approval signing — software-key signer + queue integration."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from capabledeputy.approval.model import ApprovalAction
from capabledeputy.approval.queue import (
    ApprovalQueue,
    ApprovalSignatureRequiredError,
)
from capabledeputy.approval.signer import (
    Signature,
    SignerError,
    SoftwareKeySigner,
    canonical_payload,
    load_or_create_software_key,
)
from capabledeputy.policy.labels import Label


def test_software_signer_round_trip() -> None:
    signer = SoftwareKeySigner(key=b"x" * 32, key_id="sw:test")
    msg = b"hello world"
    sig = signer.sign(msg)
    assert sig.algorithm == "hmac-sha256"
    assert sig.key_id == "sw:test"
    assert signer.verify(msg, sig) is True


def test_software_signer_rejects_tampered_message() -> None:
    signer = SoftwareKeySigner(key=b"x" * 32, key_id="sw:test")
    sig = signer.sign(b"original")
    assert signer.verify(b"tampered", sig) is False


def test_software_signer_rejects_wrong_key_id() -> None:
    signer = SoftwareKeySigner(key=b"x" * 32, key_id="sw:test")
    msg = b"hello"
    sig = signer.sign(msg)
    other_sig = Signature(
        algorithm=sig.algorithm,
        key_id="sw:other",
        signature_b64=sig.signature_b64,
    )
    assert signer.verify(msg, other_sig) is False


def test_software_signer_rejects_short_key() -> None:
    with pytest.raises(SignerError, match="32 bytes"):
        SoftwareKeySigner(key=b"short", key_id="x")


def test_load_or_create_software_key_persists(tmp_path: Path) -> None:
    path = tmp_path / "approval.key"
    s1 = load_or_create_software_key(path)
    s2 = load_or_create_software_key(path)
    msg = b"persistent"
    assert s1.verify(msg, s2.sign(msg))
    assert s1.key_id == s2.key_id


def test_load_or_create_software_key_chmods_600(tmp_path: Path) -> None:
    """The key file must not be world-readable."""
    import os
    import stat

    path = tmp_path / "approval.key"
    load_or_create_software_key(path)
    mode = os.stat(path).st_mode
    assert (mode & stat.S_IRWXG) == 0
    assert (mode & stat.S_IRWXO) == 0


def test_canonical_payload_is_deterministic() -> None:
    """Same fields → byte-identical payload regardless of dict order."""
    a = canonical_payload(
        approval_id=42,
        action="SEND_EMAIL",
        target="alice@example.com",
        payload="hi",
        labels_in=frozenset({"confidential.health", "trusted.user_direct"}),
    )
    b = canonical_payload(
        approval_id=42,
        action="SEND_EMAIL",
        target="alice@example.com",
        payload="hi",
        labels_in=frozenset({"trusted.user_direct", "confidential.health"}),
    )
    assert a == b


async def test_queue_approve_unsigned_succeeds_when_not_required() -> None:
    """Default behaviour stays the same — signing is opt-in."""
    queue = ApprovalQueue()
    request = await queue.submit(
        from_session=uuid4(),
        action=ApprovalAction.SEND_EMAIL,
        payload="test",
        target="alice@example.com",
        labels_in=frozenset({Label.TRUSTED_USER_DIRECT}),
    )
    decided = await queue.approve(request.id)
    assert decided.status.value == "approved"


async def test_queue_approve_with_signature_records_in_decision_scope() -> None:
    queue = ApprovalQueue()
    signer = SoftwareKeySigner(key=b"y" * 32, key_id="sw:scope")
    request = await queue.submit(
        from_session=uuid4(),
        action=ApprovalAction.SEND_EMAIL,
        payload="signed",
        target="alice@example.com",
        labels_in=frozenset({Label.TRUSTED_USER_DIRECT}),
    )
    msg = canonical_payload(
        approval_id=request.id,
        action=request.action.value,
        target=request.target,
        payload=request.payload,
        labels_in=frozenset(label.value for label in request.labels_in),
    )
    sig = signer.sign(msg)
    decided = await queue.approve(
        request.id,
        signature=sig,
        signer_for_verify=signer,
        require_signature=True,
    )
    assert "signature" in decided.decision_scope
    assert decided.decision_scope["signature"]["key_id"] == "sw:scope"


async def test_queue_approve_blocks_when_signature_required_but_missing() -> None:
    queue = ApprovalQueue()
    request = await queue.submit(
        from_session=uuid4(),
        action=ApprovalAction.SEND_EMAIL,
        payload="x",
        target="a@b.com",
        labels_in=frozenset(),
    )
    with pytest.raises(ApprovalSignatureRequiredError, match="requires a signature"):
        await queue.approve(request.id, require_signature=True)
    # State stays pending so the user can retry with a valid signature.
    assert queue.get(request.id).status.value == "pending"


async def test_queue_approve_blocks_on_invalid_signature() -> None:
    queue = ApprovalQueue()
    signer = SoftwareKeySigner(key=b"a" * 32, key_id="sw:a")
    other_signer = SoftwareKeySigner(key=b"b" * 32, key_id="sw:a")  # same id, wrong key
    request = await queue.submit(
        from_session=uuid4(),
        action=ApprovalAction.SEND_EMAIL,
        payload="x",
        target="a@b.com",
        labels_in=frozenset(),
    )
    msg = canonical_payload(
        approval_id=request.id,
        action=request.action.value,
        target=request.target,
        payload=request.payload,
        labels_in=frozenset(),
    )
    bogus = other_signer.sign(msg)  # wrong key, but verifier can't tell that yet
    with pytest.raises(ApprovalSignatureRequiredError):
        await queue.approve(
            request.id,
            signature=bogus,
            signer_for_verify=signer,
            require_signature=True,
        )


def test_yubikey_signer_stub_raises_clear_error() -> None:
    from capabledeputy.approval.signer import YubikeySigner

    sig = YubikeySigner()
    with pytest.raises(NotImplementedError, match="yubikey"):
        sig.sign(b"x")
