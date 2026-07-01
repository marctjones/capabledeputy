"""Remote approval envelopes: a host sends an approval-decision request
to another host on behalf of a session living on the destination side.

The envelope wraps the canonical approval payload with origin host id +
signature. The destination host verifies the signature using the
sender's known key (provided out-of-band — v0.4 doesn't ship a public-
key directory; users wire the verifier into the daemon config).

Use case: phone presents an approval modal; user taps Approve; phone
signs and ships the envelope to the laptop daemon over the local
network; laptop verifies and flips the corresponding ApprovalRequest
to APPROVED with `decided_by="host:<phone-id>"`.

This is the smallest primitive that buys "approve from your phone"
without committing to a full federated state-machine.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from capabledeputy.approval.signer import (
    ApprovalSigner,
    Signature,
    canonical_payload,
)
from capabledeputy.policy.axis_d import DecisionContext
from capabledeputy.policy.labels import LabelState

REMOTE_APPROVAL_SCHEMA_VERSION = "capdep.remote-approval.v1"


@dataclass(frozen=True)
class RemoteApprovalEnvelope:
    origin_host_id: str
    approval_id: int
    action: str
    target: str
    payload: str
    labels_in: list[str]
    signature: Signature
    schema_version: str = REMOTE_APPROVAL_SCHEMA_VERSION
    destination_host_id: str = ""
    labels_in_state: dict[str, Any] | None = None
    axis_c_effect_class: str = ""
    axis_d_context: dict[str, Any] = field(default_factory=dict)
    protocol_nonce: str = ""

    def canonical_message(self) -> bytes:
        # The signature covers the same canonical bytes a local
        # signed-approval would, plus the origin host id (so a
        # signature from host A cannot be replayed as if from host B)
        # and the structured wire metadata (so cross-host protocol
        # version / four-axis downgrade is detectable).
        body = canonical_payload(
            approval_id=self.approval_id,
            action=self.action,
            target=self.target,
            payload=self.payload,
            labels_in=frozenset(self.labels_in),
        )
        prefix = json.dumps(
            {
                "schema_version": self.schema_version,
                "origin": self.origin_host_id,
                "destination": self.destination_host_id,
                "axis": self.four_axis_wire(),
                "protocol_nonce": self.protocol_nonce,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return prefix + b"\x00" + body

    def four_axis_wire(self) -> dict[str, Any]:
        return {
            "axis_a_b_labels": self.labels_in_state,
            "axis_a_b_legacy_labels": sorted(self.labels_in),
            "axis_c_effect_class": self.axis_c_effect_class or self.action,
            "axis_d_context": self.axis_d_context,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "origin_host_id": self.origin_host_id,
            "destination_host_id": self.destination_host_id,
            "approval_id": self.approval_id,
            "action": self.action,
            "target": self.target,
            "payload": self.payload,
            "labels_in": list(self.labels_in),
            "labels_in_state": self.labels_in_state,
            "axis_c_effect_class": self.axis_c_effect_class,
            "axis_d_context": self.axis_d_context,
            "protocol_nonce": self.protocol_nonce,
            "signature": self.signature.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RemoteApprovalEnvelope:
        sig_raw = d["signature"]
        return cls(
            origin_host_id=str(d["origin_host_id"]),
            approval_id=int(d["approval_id"]),
            action=str(d["action"]),
            target=str(d["target"]),
            payload=str(d["payload"]),
            labels_in=[str(v) for v in d.get("labels_in", [])],
            signature=Signature(
                algorithm=str(sig_raw["algorithm"]),
                key_id=str(sig_raw["key_id"]),
                signature_b64=str(sig_raw["signature_b64"]),
            ),
            schema_version=str(d.get("schema_version") or REMOTE_APPROVAL_SCHEMA_VERSION),
            destination_host_id=str(d.get("destination_host_id") or ""),
            labels_in_state=d.get("labels_in_state"),
            axis_c_effect_class=str(d.get("axis_c_effect_class") or ""),
            axis_d_context=dict(d.get("axis_d_context") or {}),
            protocol_nonce=str(d.get("protocol_nonce") or ""),
        )


def pack_remote_approval(
    *,
    origin_host_id: str,
    approval_id: int,
    action: str,
    target: str,
    payload: str,
    labels_in: list[str],
    signer: ApprovalSigner,
    destination_host_id: str = "",
    labels_in_state: LabelState | None = None,
    axis_c_effect_class: str = "",
    axis_d_context: DecisionContext | None = None,
    protocol_nonce: str = "",
) -> RemoteApprovalEnvelope:
    env_skeleton = RemoteApprovalEnvelope(
        origin_host_id=origin_host_id,
        approval_id=approval_id,
        action=action,
        target=target,
        payload=payload,
        labels_in=list(labels_in),
        signature=Signature(algorithm="", key_id="", signature_b64=""),
        destination_host_id=destination_host_id,
        labels_in_state=labels_in_state.to_dict() if labels_in_state is not None else None,
        axis_c_effect_class=axis_c_effect_class,
        axis_d_context=axis_d_context.to_dict() if axis_d_context is not None else {},
        protocol_nonce=protocol_nonce,
    )
    sig = signer.sign(env_skeleton.canonical_message())
    return RemoteApprovalEnvelope(
        origin_host_id=origin_host_id,
        approval_id=approval_id,
        action=action,
        target=target,
        payload=payload,
        labels_in=list(labels_in),
        signature=sig,
        destination_host_id=destination_host_id,
        labels_in_state=labels_in_state.to_dict() if labels_in_state is not None else None,
        axis_c_effect_class=axis_c_effect_class,
        axis_d_context=axis_d_context.to_dict() if axis_d_context is not None else {},
        protocol_nonce=protocol_nonce,
    )


def unpack_remote_approval(
    envelope: RemoteApprovalEnvelope,
    *,
    verifier: ApprovalSigner,
) -> bool:
    """Verify the envelope's signature. Returns True on a valid match,
    False otherwise. Caller decides whether to apply the approval."""
    if envelope.schema_version != REMOTE_APPROVAL_SCHEMA_VERSION:
        return False
    return verifier.verify(envelope.canonical_message(), envelope.signature)
