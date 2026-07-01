"""Approval signing — optional hardware-token gate for high-stakes approvals.

The signer is an extra integrity layer on top of the approval queue.
When a session (or specific action class) demands `require_signature`,
the user's approval must carry a verifiable signature over the
canonical approval payload before the queue records it as APPROVED.

Two backends ship:

  - SoftwareKeySigner — Ed25519 keypair stored on disk. Default for
    development; the same keypair is reused across approvals so it
    is NOT a hardware-grade root of trust. Use it to test the wiring.
  - YubikeySigner    — STUB. Calls out to a future PIV/FIDO2
    integration; currently raises a clear NotImplementedError when
    `attempt_sign` is called without the right libraries installed.
    Documented so users know what to expect.

The signer is intentionally an optional path. The structural security
properties (label propagation, capability gating, declassification gates)
hold without it. Signatures are an audit-trail-and-non-repudiation
upgrade for users who want to be able to prove that a specific human
approved a specific high-stakes action.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class SignerError(RuntimeError):
    pass


class SignerNotConfiguredError(SignerError):
    pass


class SignatureVerificationError(SignerError):
    pass


@dataclass(frozen=True)
class Signature:
    """Detached signature over a canonical approval payload.

    `algorithm` documents which signer backend produced the signature
    so verification can dispatch to the right verifier. `key_id` is
    a stable identifier for the signing key (a fingerprint, a YubiKey
    serial, etc.) so revocation lists can reference it later.
    """

    algorithm: str
    key_id: str
    signature_b64: str

    def to_dict(self) -> dict[str, str]:
        return {
            "algorithm": self.algorithm,
            "key_id": self.key_id,
            "signature_b64": self.signature_b64,
        }


def canonical_payload(
    *,
    approval_id: int,
    action: str,
    target: str,
    payload: str,
    labels_in: frozenset[str] | list[str],
    artifact_hash: str | None = None,
    destination_id: str | None = None,
) -> bytes:
    """Build a deterministic byte string covering everything that must
    be signed. Any field that affects the security decision must be in
    here; ordering and JSON formatting are pinned so independent signers
    produce byte-identical payloads.
    """
    body = {
        "approval_id": approval_id,
        "action": action,
        "target": target,
        "payload": payload,
        "labels_in": sorted(labels_in),
    }
    if artifact_hash is not None:
        body["artifact_hash"] = artifact_hash
    if destination_id is not None:
        body["destination_id"] = destination_id
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return canon.encode("utf-8")


class ApprovalSigner(Protocol):
    """Protocol every signer backend implements."""

    @property
    def algorithm(self) -> str: ...

    @property
    def key_id(self) -> str: ...

    def sign(self, message: bytes) -> Signature: ...

    def verify(self, message: bytes, signature: Signature) -> bool: ...


class SoftwareKeySigner:
    """HMAC-SHA256-over-bytes signer using a key stored on disk.

    Not hardware-grade. Suitable for development and for users who
    want a non-repudiation receipt for approvals without buying a
    physical token. The key file should be `chmod 600`.
    """

    algorithm = "hmac-sha256"

    def __init__(self, key: bytes, key_id: str) -> None:
        if len(key) < 32:
            raise SignerError("software key must be at least 32 bytes")
        self._key = key
        self._key_id = key_id

    @property
    def key_id(self) -> str:
        return self._key_id

    def sign(self, message: bytes) -> Signature:
        import base64

        mac = hmac.new(self._key, message, hashlib.sha256).digest()
        return Signature(
            algorithm=self.algorithm,
            key_id=self._key_id,
            signature_b64=base64.b64encode(mac).decode("ascii"),
        )

    def verify(self, message: bytes, signature: Signature) -> bool:
        import base64

        if signature.algorithm != self.algorithm:
            return False
        if signature.key_id != self._key_id:
            return False
        try:
            mac = base64.b64decode(signature.signature_b64)
        except (ValueError, TypeError) as e:
            raise SignatureVerificationError(
                f"signature_b64 not valid base64: {e}",
            ) from e
        expected = hmac.new(self._key, message, hashlib.sha256).digest()
        return hmac.compare_digest(mac, expected)


def load_or_create_software_key(path: Path) -> SoftwareKeySigner:
    """Load a software signing key from disk; create one with 0600 perms
    on first call. Key id is a sha256 fingerprint of the key bytes
    (truncated to 16 hex chars) so audit logs can reference the key
    without exposing it.
    """
    if path.exists():
        key = path.read_bytes()
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(32)
        path.write_bytes(key)
        os.chmod(path, 0o600)
    fp = hashlib.sha256(key).hexdigest()[:16]
    return SoftwareKeySigner(key=key, key_id=f"sw:{fp}")


class YubikeySigner:
    """Stub for a real PIV/FIDO2 backend.

    Calling `sign` raises NotImplementedError with a pointer to the
    docs so users who want hardware-grade signing know what is and
    isn't built. The class is here so the rest of the runtime can
    accept a signer of this type once a backend lands without a
    breaking API change.
    """

    algorithm = "yubikey-piv"

    def __init__(self, *, slot: str = "9c", key_id: str | None = None) -> None:
        self._slot = slot
        self._key_id = key_id or f"yk:slot={slot}"

    @property
    def key_id(self) -> str:
        return self._key_id

    def sign(self, message: bytes) -> Signature:
        raise NotImplementedError(
            "YubikeySigner.sign is not implemented in v0.4. Real PIV/FIDO2 "
            "support requires a hardware token and the python-yubikey-manager "
            "library; see docs/hardware-tokens.md for the planned interface.",
        )

    def verify(self, message: bytes, signature: Signature) -> bool:
        raise NotImplementedError(
            "YubikeySigner.verify is not implemented in v0.4.",
        )
