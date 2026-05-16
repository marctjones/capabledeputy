# Demo 15: Federation — Phone-to-Laptop Approval Handoff

**Audience:** people who actually have multiple devices.
**Time:** ~3 minutes.
**Requires:** nothing beyond `uv sync`.

User on the laptop hits an approval gate. Instead of switching apps,
the phone — running its own CapableDeputy daemon — fetches the
pending approval, presents the verbatim payload, the user approves
on the phone, signs, and ships a `RemoteApprovalEnvelope` back to
the laptop. The laptop verifies the signature and applies the
approval.

This is the smallest primitive that buys "approve from your phone"
without committing to a full federated state-machine.

## What the demo proves

1. A phone-to-laptop signed envelope round-trips: pack on phone,
   verify on laptop.
2. The signature includes the origin host id — re-tagging an
   envelope as if it came from a different host invalidates the
   signature.
3. Payload tampering is detected. A man-in-the-middle that changes
   the recipient or body cannot forge a valid signature.
4. `Session.export` ships a session's state to another host as a
   signed JSON blob; tampering is caught at import time.
5. Each install has a stable random `HostId` for audit attribution.

## Walkthrough

```bash
uv run pytest tests/test_e2e_federation.py -v
```

### The envelope shape

```python
envelope = pack_remote_approval(
    origin_host_id="host:phone-abc",
    approval_id=42,
    action="SEND_EMAIL",
    target="alice@example.com",
    payload="Reply approved on phone.",
    labels_in=["trusted.user_direct"],
    signer=phone_signer,
)
```

The signed bytes include `origin_host_id` so a signature from host A
cannot be replayed against host B claiming origin B. The verifier
on the laptop calls `unpack_remote_approval(envelope, verifier=...)`;
if it returns True, the laptop applies the approval.

### Origin binding

```python
# Re-tag envelope: spoofed origin
spoofed = replace(envelope, origin_host_id="host:laptop-2")
assert unpack_remote_approval(spoofed, verifier=signer) is False
```

The signature covers the canonical bytes that include the original
host id. Changing origin → mismatch → False.

### Payload tamper

```python
tampered = replace(envelope, payload="exfiltrate-me")
assert unpack_remote_approval(tampered, verifier=signer) is False
```

### Session export for remote review

The phone needs to know what the laptop is doing. `export_session`
produces a signed JSON blob with the session's state; `import_session_export`
verifies and reconstitutes.

```python
export = export_session(session, host=laptop_host, signer=laptop_signer)
restored = import_session_export(export, verifier=laptop_signer)
assert restored.id == session.id
```

A tampered export raises `SessionExportError("signature did not validate")`.

## What this does NOT include

- **Transport**: how the phone and laptop actually exchange bytes is
  out of scope for v0.4. The primitive is the envelope; users wire
  it through SSH, a message queue, or whatever fits their threat
  model.
- **Continuous bidirectional sync**: this is v0.5+ work. The v0.4
  primitives cover the most-asked-for case (one-shot approval
  handoff).
- **Asymmetric crypto**: v0.4 uses HMAC-SHA256 with a shared key.
  Production federation between independent hosts wants Ed25519 or
  similar; the `Signature` shape is already algorithm-tagged so the
  upgrade is mechanical.

## Files

- `src/capabledeputy/federation/` — host id, export/import, remote
  approvals
- `src/capabledeputy/approval/signer.py` — software-key signer
- `tests/test_e2e_federation.py`
