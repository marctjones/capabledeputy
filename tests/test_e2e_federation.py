"""Demo 15 — federation: phone-to-laptop signed approval handoff.

User has CapableDeputy on a laptop and a phone. The laptop daemon
queues an approval (e.g. "send this email"). The phone fetches the
pending approval, presents the verbatim payload, the user approves on
the phone, and the phone signs a `RemoteApprovalEnvelope` and ships it
to the laptop. The laptop verifies the signature against the phone's
known key and applies the approval.

The signed bytes include the origin host id so a signature from host A
cannot be replayed against host B claiming origin B.
"""

from __future__ import annotations

from pathlib import Path

from capabledeputy.approval.signer import load_or_create_software_key
from capabledeputy.federation import (
    pack_remote_approval,
    unpack_remote_approval,
)
from capabledeputy.federation.export import (
    SessionExport,
    SessionExportError,
    export_session,
    import_session_export,
)
from capabledeputy.federation.host import load_or_create_host_id
from capabledeputy.session.model import Session


def test_phone_to_laptop_round_trip(tmp_path: Path) -> None:
    """Happy path: phone packs an envelope; laptop unpacks; both
    sides use the same shared key (or matched key pair in production).
    """
    phone_host = load_or_create_host_id(tmp_path / "phone_host_id")
    # Shared signing material — in production this would be an
    # asymmetric keypair; for v0.4 we use the software-key signer.
    signer = load_or_create_software_key(tmp_path / "shared.key")

    envelope = pack_remote_approval(
        origin_host_id=str(phone_host),
        approval_id=42,
        action="SEND_EMAIL",
        target="alice@example.com",
        payload="Reply approved via phone.",
        labels_in=["trusted.user_direct"],
        signer=signer,
    )
    assert unpack_remote_approval(envelope, verifier=signer) is True


def test_origin_binding_prevents_replay(tmp_path: Path) -> None:
    """Re-tagging an envelope with a different origin host id
    invalidates the signature."""
    from capabledeputy.federation.remote_approval import RemoteApprovalEnvelope

    signer = load_or_create_software_key(tmp_path / "k.key")
    envelope = pack_remote_approval(
        origin_host_id="host:phone-1",
        approval_id=1,
        action="SEND_EMAIL",
        target="x@example.com",
        payload="hi",
        labels_in=[],
        signer=signer,
    )
    spoofed = RemoteApprovalEnvelope(
        origin_host_id="host:laptop-2",  # wrong origin
        approval_id=envelope.approval_id,
        action=envelope.action,
        target=envelope.target,
        payload=envelope.payload,
        labels_in=envelope.labels_in,
        signature=envelope.signature,
    )
    assert unpack_remote_approval(spoofed, verifier=signer) is False


def test_payload_tamper_detected(tmp_path: Path) -> None:
    from capabledeputy.federation.remote_approval import RemoteApprovalEnvelope

    signer = load_or_create_software_key(tmp_path / "k.key")
    envelope = pack_remote_approval(
        origin_host_id="host:phone",
        approval_id=1,
        action="SEND_EMAIL",
        target="alice@example.com",
        payload="benign",
        labels_in=[],
        signer=signer,
    )
    tampered = RemoteApprovalEnvelope(
        origin_host_id=envelope.origin_host_id,
        approval_id=envelope.approval_id,
        action=envelope.action,
        target=envelope.target,
        payload="exfiltrate-me",  # changed
        labels_in=envelope.labels_in,
        signature=envelope.signature,
    )
    assert unpack_remote_approval(tampered, verifier=signer) is False


def test_session_export_for_remote_review(tmp_path: Path) -> None:
    """Session export lets the phone show what's happening on the
    laptop. Tampered exports are rejected at import time."""
    host = load_or_create_host_id(tmp_path / "h")
    signer = load_or_create_software_key(tmp_path / "k")
    session = Session.new(intent="prescription review", owner="marc")
    export = export_session(session, host=host, signer=signer)
    restored = import_session_export(export, verifier=signer)
    assert restored.id == session.id
    assert restored.intent == "prescription review"

    tampered = SessionExport(
        host_id=export.host_id,
        exported_at=export.exported_at,
        session={**export.session, "intent": "spoofed"},
        signature=export.signature,
    )
    import pytest as _p

    with _p.raises(SessionExportError, match="signature"):
        import_session_export(tampered, verifier=signer)


def test_host_id_persists_across_loads(tmp_path: Path) -> None:
    """The host id is a stable random hex string per install. Phone
    and laptop each have their own; both attribute audit events to
    their own id."""
    h1 = load_or_create_host_id(tmp_path / "host_id", display_name="laptop")
    h2 = load_or_create_host_id(tmp_path / "host_id", display_name="laptop")
    assert h1.value == h2.value

    other = load_or_create_host_id(tmp_path / "other_host_id", display_name="phone")
    assert other.value != h1.value
