# Synthetic supervised-email native evidence

These are final PASS artifacts from isolated macOS background Accessibility
runs on 2026-10-07. All mail content/accounts are synthetic. No production
daemon, live mailbox, network SMTP destination, keyboard/mouse event or
foreground activation was used.

| Directory | Outcome | Background checks | Fake SMTP attempts |
| --- | --- | --- | --- |
| accepted | SMTP accepted; recipient delivery unverified | 92 | 1 |
| failed | Definite pre-submission failure | 91 | 1 |
| unknown | Unknown submission followed by explicit provider verification | 110 | 1; no retry |

AX snapshot line endings/trailing whitespace are normalized for repository
formatting; the original temporary snapshots remain unchanged.

Each directory preserves the result and build manifest plus exact-review,
restart-recovery and readable-transcript AX snapshots. Unknown also preserves
provider-verification evidence. Build hashes identify the actual test artifacts;
manifests mark the tested source as a dirty working tree. This evidence does
not establish signed distribution, real-provider acceptance or model quality.

Reproduce with `scripts/test-email-mvp-gui.py` and an empty artifact directory.
Use `--smtp-outcome accepted`, `failed_before_submission` or `outcome_unknown`.
The driver serializes native runs, creates unique app identities and requires
the runner host's macOS Accessibility permission. Keep all account effects
synthetic. See [the complete verification record](../../supervised-email-mvp-evidence-2026-10-07.md)
for backend checks and remaining acceptance boundaries.
