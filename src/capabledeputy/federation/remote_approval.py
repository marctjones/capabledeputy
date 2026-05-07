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
from dataclasses import dataclass
from typing import Any

from capabledeputy.approval.signer import (
    ApprovalSigner,
    Signature,
    canonical_payload,
)


@dataclass(frozen=True)
class RemoteApprovalEnvelope:
    origin_host_id: str
    approval_id: int
    action: str
    target: str
    payload: str
    labels_in: list[str]
    signature: Signature

    def canonical_message(self) -> bytes:
        # The signature covers the same canonical bytes a local
        # signed-approval would, plus the origin host id (so a
        # signature from host A cannot be replayed as if from host B).
        body = canonical_payload(
            approval_id=self.approval_id,
            action=self.action,
            target=self.target,
            payload=self.payload,
            labels_in=frozenset(self.labels_in),
        )
        prefix = json.dumps({"origin": self.origin_host_id}, sort_keys=True).encode("utf-8")
        return prefix + b"\x00" + body

    def to_dict(self) -> dict[str, Any]:
        return {
            "origin_host_id": self.origin_host_id,
            "approval_id": self.approval_id,
            "action": self.action,
            "target": self.target,
            "payload": self.payload,
            "labels_in": list(self.labels_in),
            "signature": self.signature.to_dict(),
        }


def pack_remote_approval(
    *,
    origin_host_id: str,
    approval_id: int,
    action: str,
    target: str,
    payload: str,
    labels_in: list[str],
    signer: ApprovalSigner,
) -> RemoteApprovalEnvelope:
    env_skeleton = RemoteApprovalEnvelope(
        origin_host_id=origin_host_id,
        approval_id=approval_id,
        action=action,
        target=target,
        payload=payload,
        labels_in=list(labels_in),
        signature=Signature(algorithm="", key_id="", signature_b64=""),
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
    )


def unpack_remote_approval(
    envelope: RemoteApprovalEnvelope,
    *,
    verifier: ApprovalSigner,
) -> bool:
    """Verify the envelope's signature. Returns True on a valid match,
    False otherwise. Caller decides whether to apply the approval."""
    return verifier.verify(envelope.canonical_message(), envelope.signature)
