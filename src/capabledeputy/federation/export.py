"""Session export/import: deterministic, signed JSON for federation.

A session export is a JSON envelope with:

  - `host_id`: the originating CapableDeputy install.
  - `exported_at`: ISO timestamp.
  - `session`: the session's `to_dict()` payload.
  - `audit_excerpt`: optional list of audit events the recipient
    needs to replay the session's label/capability state — currently
    a no-op (session.to_dict() already carries the relevant state).
  - `signature`: a Signature object covering the canonical bytes of
    everything above.

Imports validate the signature against the recipient's known
public-key registry. v0.4 uses the same software-key signer as
approvals; v0.5+ will swap in asymmetric keys for true cross-host
identity.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from capabledeputy.approval.signer import (
    ApprovalSigner,
    Signature,
    SignatureVerificationError,
)
from capabledeputy.federation.host import HostId
from capabledeputy.session.model import Session


class SessionExportError(RuntimeError):
    pass


@dataclass(frozen=True)
class SessionExport:
    host_id: str
    exported_at: str
    session: dict[str, Any]
    signature: Signature

    def to_dict(self) -> dict[str, Any]:
        return {
            "host_id": self.host_id,
            "exported_at": self.exported_at,
            "session": self.session,
            "signature": self.signature.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SessionExport:
        sig = d.get("signature") or {}
        return cls(
            host_id=str(d["host_id"]),
            exported_at=str(d["exported_at"]),
            session=d["session"],
            signature=Signature(
                algorithm=str(sig["algorithm"]),
                key_id=str(sig["key_id"]),
                signature_b64=str(sig["signature_b64"]),
            ),
        )


def _canonical_export_bytes(host_id: str, exported_at: str, session: dict[str, Any]) -> bytes:
    return json.dumps(
        {"host_id": host_id, "exported_at": exported_at, "session": session},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def export_session(
    session: Session,
    *,
    host: HostId,
    signer: ApprovalSigner,
) -> SessionExport:
    exported_at = datetime.now(UTC).isoformat()
    payload = _canonical_export_bytes(host.value, exported_at, session.to_dict())
    sig = signer.sign(payload)
    return SessionExport(
        host_id=host.value,
        exported_at=exported_at,
        session=session.to_dict(),
        signature=sig,
    )


def import_session_export(
    export: SessionExport,
    *,
    verifier: ApprovalSigner,
) -> Session:
    """Verify the export's signature and reconstitute the Session.

    The caller is expected to provide a `verifier` that knows the
    sender's key (matching key_id). On signature failure raises
    SessionExportError; the caller decides whether to log and drop
    or to alert.
    """
    payload = _canonical_export_bytes(export.host_id, export.exported_at, export.session)
    try:
        ok = verifier.verify(payload, export.signature)
    except SignatureVerificationError as e:
        raise SessionExportError(f"signature verification raised: {e}") from e
    if not ok:
        raise SessionExportError(
            f"signature did not validate for export from host_id={export.host_id}",
        )
    try:
        return Session.from_dict(export.session)
    except (KeyError, ValueError) as e:
        raise SessionExportError(f"export session payload malformed: {e}") from e
