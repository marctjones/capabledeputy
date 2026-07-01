"""Federation primitives: HostId, session export/import, remote approval."""

from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.approval.signer import (
    SoftwareKeySigner,
    load_or_create_software_key,
)
from capabledeputy.federation import (
    REMOTE_APPROVAL_SCHEMA_VERSION,
    SessionExportError,
    export_session,
    import_session_export,
    load_or_create_host_id,
    pack_remote_approval,
    unpack_remote_approval,
)
from capabledeputy.federation.export import SessionExport
from capabledeputy.session.model import Session


def test_host_id_persists_across_loads(tmp_path: Path) -> None:
    path = tmp_path / "host_id"
    h1 = load_or_create_host_id(path, display_name="laptop")
    h2 = load_or_create_host_id(path, display_name="laptop")
    assert h1.value == h2.value
    assert len(h1.value) == 32  # 16 bytes hex


def test_export_round_trips_session(tmp_path: Path) -> None:
    host = load_or_create_host_id(tmp_path / "host_id", display_name="phone")
    signer = load_or_create_software_key(tmp_path / "key")
    session = Session.new(intent="test", owner="alice")

    export = export_session(session, host=host, signer=signer)
    restored = import_session_export(export, verifier=signer)
    assert restored.id == session.id
    assert restored.intent == "test"


def test_import_rejects_tampered_session(tmp_path: Path) -> None:
    host = load_or_create_host_id(tmp_path / "host_id")
    signer = load_or_create_software_key(tmp_path / "key")
    session = Session.new(intent="original")
    export = export_session(session, host=host, signer=signer)

    # Tamper with the inner session payload.
    tampered = SessionExport(
        host_id=export.host_id,
        exported_at=export.exported_at,
        session={**export.session, "intent": "spoofed"},
        signature=export.signature,
    )
    with pytest.raises(SessionExportError, match="signature"):
        import_session_export(tampered, verifier=signer)


def test_import_rejects_signature_from_wrong_key(tmp_path: Path) -> None:
    host = load_or_create_host_id(tmp_path / "host_id")
    sender = load_or_create_software_key(tmp_path / "sender_key")
    other = SoftwareKeySigner(key=b"z" * 32, key_id=sender.key_id)  # same id, wrong bytes
    session = Session.new()
    export = export_session(session, host=host, signer=sender)

    # Verifier built from a different key with the same key_id will fail.
    with pytest.raises(SessionExportError):
        import_session_export(export, verifier=other)


def test_remote_approval_round_trips(tmp_path: Path) -> None:
    signer = load_or_create_software_key(tmp_path / "key")
    envelope = pack_remote_approval(
        origin_host_id="host:phone-abc",
        approval_id=42,
        action="SEND_EMAIL",
        target="alice@example.com",
        payload="hi",
        labels_in=["trusted.user_direct"],
        signer=signer,
    )
    assert unpack_remote_approval(envelope, verifier=signer) is True


def test_remote_approval_origin_binding(tmp_path: Path) -> None:
    """A signature from host A must not validate when the envelope
    claims origin host B — the signed bytes include the origin host id
    so cross-host replay is impossible."""
    from capabledeputy.federation.remote_approval import RemoteApprovalEnvelope

    signer = load_or_create_software_key(tmp_path / "key")
    envelope = pack_remote_approval(
        origin_host_id="host:phone-abc",
        approval_id=42,
        action="SEND_EMAIL",
        target="alice@example.com",
        payload="hi",
        labels_in=["trusted.user_direct"],
        signer=signer,
    )
    rebound = RemoteApprovalEnvelope(
        origin_host_id="host:laptop-xyz",  # claim a different origin
        approval_id=envelope.approval_id,
        action=envelope.action,
        target=envelope.target,
        payload=envelope.payload,
        labels_in=envelope.labels_in,
        signature=envelope.signature,
    )
    assert unpack_remote_approval(rebound, verifier=signer) is False


def test_remote_approval_payload_tamper(tmp_path: Path) -> None:
    """Tampering with the inner approval payload invalidates the sig."""
    from capabledeputy.federation.remote_approval import RemoteApprovalEnvelope

    signer = load_or_create_software_key(tmp_path / "key")
    envelope = pack_remote_approval(
        origin_host_id="host:phone-abc",
        approval_id=42,
        action="SEND_EMAIL",
        target="alice@example.com",
        payload="original",
        labels_in=["trusted.user_direct"],
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


def test_remote_approval_structured_four_axis_wire_round_trips(tmp_path: Path) -> None:
    from capabledeputy.policy.axis_d import DecisionContext
    from capabledeputy.policy.labels import CategoryTag, LabelState, ProvenanceLevel, ProvenanceTag
    from capabledeputy.policy.tiers import Tier

    signer = load_or_create_software_key(tmp_path / "key")
    labels = LabelState(
        a=frozenset(
            {
                CategoryTag(
                    category="financial",
                    tier=Tier.REGULATED,
                    risk_ids=("RISK-FINANCE",),
                    assignment_provenance="operator-declared",
                ),
            },
        ),
        b=frozenset({ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT)}),
    )
    ctx = DecisionContext(
        initiator="marc",
        counterparty="bank.example",
        expectedness="expected",
        reversibility_degree="irreversible",
        reversibility_agent="external",
    )
    envelope = pack_remote_approval(
        origin_host_id="host:phone-abc",
        destination_host_id="host:laptop-xyz",
        approval_id=42,
        action="QUEUE_PURCHASE",
        target="bank.example",
        payload="wire funds",
        labels_in=["confidential.financial"],
        labels_in_state=labels,
        axis_c_effect_class="TRANSACT",
        axis_d_context=ctx,
        protocol_nonce="nonce-1",
        signer=signer,
    )
    assert envelope.schema_version == REMOTE_APPROVAL_SCHEMA_VERSION
    wire = envelope.four_axis_wire()
    assert wire["axis_c_effect_class"] == "TRANSACT"
    assert wire["axis_d_context"]["counterparty"] == "bank.example"
    assert wire["axis_a_b_labels"]["a"][0]["category"] == "financial"
    assert unpack_remote_approval(envelope, verifier=signer) is True
    rebound = type(envelope).from_dict(envelope.to_dict())
    assert unpack_remote_approval(rebound, verifier=signer) is True


def test_remote_approval_schema_downgrade_rejected(tmp_path: Path) -> None:
    signer = load_or_create_software_key(tmp_path / "key")
    envelope = pack_remote_approval(
        origin_host_id="host:phone-abc",
        approval_id=42,
        action="SEND_EMAIL",
        target="alice@example.com",
        payload="hi",
        labels_in=[],
        signer=signer,
    )
    downgraded = type(envelope)(
        origin_host_id=envelope.origin_host_id,
        approval_id=envelope.approval_id,
        action=envelope.action,
        target=envelope.target,
        payload=envelope.payload,
        labels_in=envelope.labels_in,
        signature=envelope.signature,
        schema_version="capdep.remote-approval.v0",
    )
    assert unpack_remote_approval(downgraded, verifier=signer) is False
