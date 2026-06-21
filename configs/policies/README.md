# Decision-inspector policy scripts

Operator-authored policy-refinement scripts (Issue #46/#47). Each script
defines `inspect(action, session, proposed_outcome)` and returns
`relax(...)`, `tighten(...)`, or `abstain()`. They run AFTER the standard
policy decision and compose monotonically (TIGHTEN beats RELAX); a relax is
bounded by the envelope cell and can never cross a structural DENY floor.

Enable them with a `decision_inspectors:` block in your daemon config — see
[`../decision-inspectors.example.yaml`](../decision-inspectors.example.yaml).
The `personal-assistant` preset enables the production-safe tightening scripts
by default.

## What a script can see (hermetic — no clock, no I/O, no host objects)

```
action           = {"kind": str, "target": str, "amount": int|None,
                    "relationship_groups": [str],
                    "reversibility": {"degree": str, "agent": str}}
session          = {"purpose": str, "categories": [str], "tiers": [str],
                    "provenance": [str], "risk_preference": str,
                    "reversibility": {"degree": str, "agent": str},
                    "history": {"counts_by_kind": {kind: int},
                                "used_kinds": [str], "total_uses": int}}
proposed_outcome = {"decision": str, "rule": str, "reason": str}
```

`session["history"]` (#48) is a bounded, read-only, session-*cumulative*
summary — enough for frequency caps ("N sends this session"). It is
clock-free (scripts have no clock), so time-windowed rates
("> N / hour") are not expressible yet.

`decision` / a relax-or-tighten `to` is one of:
`"allow" | "require_approval" | "override_required" | "deny"`.

## Runtimes

- `starlark` (default) — the real language-level sandbox (no import, no
  builtins, no I/O). Requires the `capabledeputy[starlark]` extra.
- `python-reference` — AST-filtered Python ref host for prototyping only.
  **Not a security boundary** — never use for untrusted policy.

## Current limitations (tracked)

- **No clock** — time-of-day logic (e.g. after-hours) must use the
  `after_hours_purchase_tightener` builtin, not a script. Time-*windowed*
  frequency ("> N / hour") is likewise not expressible; cumulative
  session counts via `session["history"]` are (#48, done).
- **No clock** — use the `after_hours_purchase_tightener` builtin for
  time-of-day logic. Relationship-aware relax is available via
  `action["relationship_groups"]`; reversible-write relax is available via
  `session["reversibility"]`.

## Shipped starters

- `sensitive_egress_confirm.star` — TIGHTEN: add a confirmation prompt to
  egress, drafts, chat, purchases, and calendar materialization that would
  auto-allow while the session carries restricted/regulated/prohibited data.
- `local_app_confirm.star` — TIGHTEN: require first-use approval for local
  AppleScript/macOS active automation, clipboard access, drafts, document
  edits/exports, presentation control, and calendar mutation.
- `frequency_cap.star` — TIGHTEN: require approval once a send/draft/local-app/
  document/calendar action kind has been used enough times in the session to
  look like a runaway loop (uses `session["history"]`, #48).
- `purpose_scoped_relax.star` — RELAX: example only; currently limited to local
  notifications for the `research` purpose. Do not enable broader relax rules
  without workflow-specific tests.
- `relationship_relax.star` — RELAX: allow email to a recipient in a vetted
  relationship group (uses `action["relationship_groups"]`, #47). Keep this
  opt-in; the personal-assistant preset does not enable relax scripts.
- `reversible_write_auto.star` — RELAX: allow write-like actions only when the
  current action is already proven `reversible/system` and the session carries
  no high-tier data. Keep this opt-in until the operator has source bindings and
  rollback paths they trust.
