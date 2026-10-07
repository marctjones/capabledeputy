# Personal-email daily-use acceptance (#479)

Status on 2026-10-07: preparation and synthetic recovery verified; real-provider
acceptance and the seven-day trial have not started. This does not reopen the
bounded MVP or certify the whole product. Use one configured personal IMAP/SMTP
account, one recipient and plain-text mail. No work mail, attachments or reply-all.

## Before the first real read

1. Check the running daemon revision against the tested checkout. The recorder
   refuses a mismatch. A matching dirty revision is not an immutable build:
   retain the build manifest and reviewed patch separately. Rebuild/restart the
   daemon at a safe idle point before using newly changed exposure enforcement.
2. Review #454's actual storage posture: tool-return audits contain commitments,
   but labeled session history, source snapshots, drafts, operation envelopes,
   diagnostics and older logs can contain private data. Inventory historical
   records by counts/types, without dumping payloads. Choose retention/export
   deliberately; never silently delete old evidence. The recorder's private
   metadata report does not make daemon storage content-free.
3. Review #464 for the actual configured planner, extractor, provider and source
   ingress. Choose the mandatory handling modes that satisfy the operator's
   requirements. A local extractor or `dual_llm` selection alone does not prove
   planner blindness. If the requirement cannot be met, record a blocked task;
   do not widen modes, use shadow enforcement or switch to a cloud fallback.

Preflight has no session creation, grant, read or send:

```sh
.venv/bin/python scripts/test-supervised-mail-live.py \
  --expected-revision "$(git rev-parse HEAD)" \
  --report /tmp/capdep-email-preflight.json
```

After the above review, opt in explicitly. The following example permits only
`dual_llm`; select it only if it meets the reviewed exposure requirement:

```sh
.venv/bin/python scripts/test-supervised-mail-live.py \
  --expected-revision "$(git rev-parse HEAD)" \
  --report /tmp/capdep-email-day1-read.json \
  --live --retention-reviewed --required-mode dual_llm
```

Reports use new exclusive files with mode 0600. The recorder creates a dedicated
session, checks strict enforcement, the retained constraint and no send capability,
adds a five-minute IMAP read grant, requests two recent messages, checks returned
audit commitments, and aborts its session even on failure. Aborting prevents more
activity; it does not erase retained messages. Failed cleanup is a failed run.
Only typed counts, booleans, known tool/decision tokens and revision metadata are
exported. Exceptions export their class, never provider text. Output status
`read_completed_quality_unreviewed` proves neither factual correctness nor inbox
receipt. Reports are not automatically published.

## Exact controlled send

Use the native app's source selection, draft edit and exact review. The operator
must inspect the account, From, recipient, subject, body and thread headers in
that particular pending review. Neither this issue nor the recorder authorizes
SMTP. Keep the final content private; retain opaque operation/review references
and the build identity in private evidence. Record SMTP acceptance separately
from a recipient-observed receipt. If uncertain, inspect the provider and use
explicit operator reconciliation. Never click approval again as a retry or infer
receipt from `smtp_accepted`. A fresh attempt requires a newer edited draft and
fresh exact review where policy allows it.

## Seven elapsed days

Begin Day 1 only after a real, supervised workflow completes. Preserve failures
and blocked legitimate tasks; do not count synthetic runs or preflight as use.
For each day, privately record the following metadata for a summary and, when
useful, a separately reviewed reply. Zero replies is an honest count, not evidence
that the reply path worked. Record each implementation/model change so evidence
from different revisions remains distinguishable.

| Field | Recording rule |
| --- | --- |
| Date, day number, revision/build, model | Exact identities; no account names or paths in public exports |
| Summary/reply attempts and completions | Separate attempted, completed and blocked counts |
| Source and coverage accuracy | Human-checked counts, incomplete/truncated counts; leave unknown until checked |
| Edits before acceptance | Count corrections separately from stylistic edits |
| Reviews | Necessary and unnecessary prompt counts, judged by the operator |
| Blocked legitimate tasks | Count and bounded reason code: authority, exposure, credentials, source, review, transport, storage |
| Latency and recovery effort | Seconds per workflow and operator minutes spent recovering |
| Unauthorized effects/disclosures | Separate observed incidents, tested denials and unobserved/unknown cases |
| Submission and receipt | Separate SMTP acceptance, failed-before-submission, unknown and observed receipt counts |

Keep raw sources/replies/screenshots out of Git and issue comments. Public
summaries contain only reviewed aggregate metadata. Seven days and human factual
reviews cannot be replaced by automated checks. Feed the email-specific results
into #451/#452/#349 without declaring general product readiness or user popularity.

## Recovery evidence and remaining provider cases

| Case | Current automated evidence | Remaining live evidence |
| --- | --- | --- |
| UIDVALIDITY changes | `test_socket_changed_empty_mailbox_epoch_invalidates_old_sources_after_restart` | Provider epoch/reset behavior |
| Source deletion without epoch change | Not established by the epoch test | Define retained snapshot behavior and test provider deletion explicitly |
| Revoked/expired capability | `test_hostile_planner_cannot_send_with_inert_authority` | Account read capability revocation during a real run |
| Revoked credentials | SMTP errors tested; not proof of provider revocation | Controlled credential invalidation and recovery |
| Expired/edited reviews | Exact review and concurrent edit tests | Native review friction during daily use |
| Two clients | `test_concurrent_distinct_approvals_submit_once` | Optional two-client provider observation |
| Cancellation | `test_socket_cancel_during_mail_fetch_does_not_create_reply_or_send` | Live cancellation and native status |
| Restart/uncertainty | Persisted receipt, interrupted claim and explicit reconciliation tests; archived native app restart | Real provider/native recovery without replay |
| Claim persistence fails | New `test_journal_claim_failure_prevents_smtp` | Storage incident operation runbook |
| Status persistence fails after SMTP | New `test_status_storage_failure_after_smtp_never_replays`, transient and sustained failures, restart | Provider reconciliation of unknown status |

The storage tests inject failures through the real daemon handler with synthetic
transport. They establish no-send-before-claim and no-replay-after-claim; they do
not emulate every filesystem failure or prove recipient receipt.

## Evidence on 2026-10-07

- Saved metadata-only preflight:
  `docs/evidence/email-daily-use-2026-10-07/preflight.json`.
  The daemon reported revision `c2d5643a8eb58c7e24d97b7e3562e95516407385`, dirty;
  the expected tested checkout was `0c88089070905683f83ae934af20788e454289b0`.
  The run blocked before session creation or mailbox access.
- Recovery/recorder and existing email acceptance, journal, serialized SMTP and
  delivery tests: **78 passed**. Focused Ruff and Pyright checks passed.
- Native/synthetic MVP evidence remains in
  `docs/supervised-email-mvp-evidence-2026-10-07.md`; it is not real-provider proof.
- Real reads, controlled sends, recipient receipts and elapsed trial days in this
  follow-up: **0**. #479 remains open.
