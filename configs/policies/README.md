# Decision-inspector policy scripts

Operator-authored policy-refinement scripts (Issue #46/#47). Each script
defines `inspect(action, session, proposed_outcome)` and returns
`relax(...)`, `tighten(...)`, or `abstain()`. They run AFTER the standard
policy decision and compose monotonically (TIGHTEN beats RELAX); a relax is
bounded by the envelope cell and can never cross a structural DENY floor.

Enable them with a `decision_inspectors:` block in your daemon config — see
[`../decision-inspectors.example.yaml`](../decision-inspectors.example.yaml).

## What a script can see (hermetic — no clock, no I/O, no host objects)

```
action           = {"kind": str, "target": str, "amount": int|None}
session          = {"purpose": str, "categories": [str], "tiers": [str],
                    "provenance": [str], "risk_preference": str}
proposed_outcome = {"decision": str, "rule": str, "reason": str}
```

`decision` / a relax-or-tighten `to` is one of:
`"allow" | "require_approval" | "override_required" | "deny"`.

## Runtimes

- `starlark` (default) — the real language-level sandbox (no import, no
  builtins, no I/O). Requires the `capabledeputy[starlark]` extra.
- `python-reference` — AST-filtered Python ref host for prototyping only.
  **Not a security boundary** — never use for untrusted policy.

## Current limitations (tracked)

- **No clock** — time-of-day logic (e.g. after-hours) must use the
  `after_hours_purchase_tightener` builtin, not a script.
- **No history** — frequency / aggregation logic ("> N sends/hour",
  "N reads → bump tier") needs the read-only session-history summary from
  #48, not yet threaded into `session`.
- **No relationship groups / reversibility fields** in `session` yet — so
  relationship-aware relax and reversible-write auto are not yet
  expressible as scripts (use rules/builtins for now).

## Shipped starters

- `sensitive_egress_confirm.star` — TIGHTEN: add a confirmation prompt to
  egress that would auto-allow while the session carries restricted/
  regulated data.
- `purpose_scoped_relax.star` — RELAX: grant autonomy for a benign,
  opted-in purpose (edit the purpose name + action kinds for your setup).
