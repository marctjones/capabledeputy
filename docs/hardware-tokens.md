# Hardware-token approval signing

Approvals are a Clark-Wilson well-formed transaction (DESIGN.md §3, §8).
The structural security properties hold without any signature — but
for high-stakes operations a non-repudiable signature over the
approval payload provides an audit-trail upgrade: cryptographic proof
that a specific human approved a specific action.

## Status

| Backend | Status | What it offers |
|---|---|---|
| `SoftwareKeySigner` | shipped (v0.4) | HMAC-SHA256 over the canonical approval payload. Keypair persisted under 0600. Suitable for development; **not** hardware-grade. |
| `YubikeySigner` (PIV/FIDO2) | stub interface; sign/verify raise `NotImplementedError` | Real implementation needs `python-yubikey-manager` and a physical token. Class exists so the rest of the runtime can accept this signer type once the backend lands without an API break. |
| Smart-card / PKCS#11 | not started | Future v0.5+ work. |

## Canonical payload

`canonical_payload(approval_id, action, target, payload, labels_in)`
produces a deterministic byte string covering every field that affects
the security decision. Order and JSON formatting are pinned so two
independent signers (e.g., the YubiKey on a phone and the daemon on a
laptop, once federation lands) produce byte-identical payloads.

The payload deliberately excludes:

- `decided_by` (decided by the act of signing).
- `decision_at` (set after verification succeeds).
- `decision_scope` (which holds the signature itself, among other things).

## Software keys (development)

```python
from capabledeputy.approval.signer import load_or_create_software_key

signer = load_or_create_software_key(Path("~/.config/capabledeputy/approval.key"))
```

The key file is created with `0600` perms and a sha256 fingerprint is
used as the `key_id` so audit logs can reference the key without
exposing its bytes.

## Approval-queue integration

```python
from capabledeputy.approval.signer import canonical_payload

msg = canonical_payload(
    approval_id=request.id,
    action=request.action.value,
    target=request.target,
    payload=request.payload,
    labels_in=frozenset(label.value for label in request.labels_in),
)
sig = signer.sign(msg)

await queue.approve(
    request.id,
    signature=sig,
    signer_for_verify=signer,
    require_signature=True,
)
```

If `require_signature=True` and either the signature is missing or
verification fails, the queue raises `ApprovalSignatureRequiredError`
and **leaves the request PENDING** — so the user can retry with a
valid signature rather than losing the request.

The signature is recorded in `decision_scope["signature"]` and
audited alongside the approval, so a future replay can re-verify it.

## Threat model

What signing buys you:

- **Non-repudiation**: a third party can verify that a specific
  keypair approved a specific action. Useful for incident response.
- **Defence-in-depth against approval-UI compromise**: even if
  malware on the host could click "Approve," a hardware token
  provides a step the malware cannot satisfy without physical access.

What signing does NOT buy you:

- Confidentiality of labeled data — that is the existing label-and-
  capability model's job.
- Protection against the LLM lying about *what* to approve. The
  approval payload is shown verbatim per DESIGN.md §8.2; the user is
  still the one deciding whether the payload is what they meant.

## Roadmap

The `YubikeySigner` class shape is final; only the body of `sign()`
and `verify()` are missing. A real implementation will use
[python-yubikey-manager](https://github.com/Yubico/yubikey-manager)
to drive the PIV applet, with the slot configurable per session class.

FIDO2-only tokens (no PIV) will get a parallel `Fido2Signer` since the
challenge-response shape is different from PIV signing.
