# Live email MVP continuation — safe handoff, 2026-10-07

Coordinator requested a fresh smaller-context session. This session has stopped
new work. No exact send was approved; no SMTP attempt or inbox receipt occurred.
No real summary completed and Day 1 has not begun. #479 remains open. The current
source changes are **uncommitted**, tested working-tree changes, not a release.

## Stable operational endpoint

The mail-only operational experiment had already started when the handoff request
arrived. It also failed under the unchanged resource guard. The original launchd
configuration was restored from `/tmp/capdep-live-baseline/launchd-preserved.plist`
and restarted to complete cleanup. Content tracing remains disabled via
`CAPDEP_CHAT_DEBUG=0`; existing audit and chat-trace files were restricted to 0600.
No historical log was erased, rotated, exported, or declared remediated.
The original user daemon config, credentials, settings, and unrelated sessions
were preserved. Guard limits/policy/Starlark were never widened.

Final cleanup report: `../evidence/email-daily-use-2026-10-07/runtime-unblocking/handoff-cleanup.json`.
Two remaining dedicated acceptance sessions were aborted. At that observation:
active turns = 0, coordinator active workflows = 0. Earlier interrupted sessions
were also closed by this lane. The process is idle with the restored broad
provider config; no acceptance client remains running. MLX weights load lazily
on inference, so descriptors being present do not establish resident weights.

Runtime identity: dirty startup Git `8589346da5e311eb595539d8d74be7058b876b2c`,
version 0.58.0, mtime manifest `7444243677c33091`, Python source-byte SHA256
`1834907d977fe2a5dba696fa9cb3e41101eb7bf5fbf38bb3e1033e9ebf603d8f`.
This is a startup source snapshot, not proof every module was imported or an
immutable build. Recheck it before any future read after source edits.
The full current dirty patch is private at
`/tmp/capdep-live-baseline/full-current.patch`; its hash and original runtime
backup/config hashes are in the archived `handoff-manifest.json`.
Initial shared-checkout baseline: `/tmp/capdep-live-baseline/baseline.patch` and
`status.txt`. Do not reset, switch branches, or stage unrelated changes.

## Evidence and defects addressed

- `daemon.code_version` now reports a source-byte SHA256 including untracked
  Python modules. The recorder refuses matching-Git but differing source bytes.
- Recorder explicitly selects an allowed mode, keeps strict enforcement, revokes
  inherited unbounded IMAP grants before its five-minute grant, never grants send,
  and preserves cleanup failure classification. The neutral user request now
  exercises the shipped scope correction instead of defeating its recognizer.
- Local read-only retention inspector counts known storage tables, legacy raw
  tool-return records and permission metadata without rendering payloads.
  Inventory found 2,820 historical raw tool-return records, 54 commitments at
  inventory time, and retained private session/draft/diagnostic data. Filesystem
  permission changes do not erase copies or certify full retention/access policy.
- One diagnostic projection mistakenly included a completed private turn in tool
  output. It was corrected and counted as a privacy failure. No payload is in the
  handoff, Git evidence, issues or screenshots; do not republish it. The projection
  was a new diagnostic failure, distinct from historical raw audit records.
- Same-epoch provider deletion deliberately retains daemon-captured labeled
  snapshots and their thread identity, including after restart. No automatic
  invalidation/replay is inferred. New real-handler tests prove that behavior.
- Credential-failure tests verify no extraction/new snapshot on failed read,
  explicit restoration/request for recovery, and no send. A concrete IMAP defect
  was fixed: failed login now closes its unowned connection before propagating.
  Actual provider credential revocation was not performed or claimed.
- Source snapshots/projections now retain the Date header.
- MLX prefill is bounded to 512 by default (constructor configurable), with
  allowlisted numeric per-generation metrics. Identical synthetic 6,018-token
  prompts passed the unchanged guard at both 2,048 and 512 batches: 6.115 vs
  5.904 GB reported peak MLX memory, 25.83 vs 28.49 seconds. These are MLX peak
  allocation values, not whole-host RSS; guard samples are saved separately.
- Production first planner contexts offered 14 tools and approximately 7,100
  estimated tokens. Explicit neutral recent-email summaries now offer only the
  projection tool, preserving the security prompt and daemon authorization.
  Trusted explicit count bounds provider max_results. Explicit date/query/folder
  requests, non-neutral requests, follow-ups and reply routes remain on the
  existing wider path. Tests retain exact explicit-date behavior and verify a
  direct hostile dispatch still hits the policy denial even if planning refuses
  earlier through visibility.
- Actual cached Qwen3 8B synthetic workflow passed under the guard: 5 responses,
  planner input 3,840 then 4,397 measured tokens, peak 5.647 GB; both fixture
  topics/dates and exact fake SMTP retained. This does not prove live usefulness.
- Several live attempts failed before any tool return under sustained host-memory
  warning; they are failed attempts with cleanup failures, not successful reads.
  One retry correctly refused Git revision drift during native commits.
  The reversible mail-only config is saved privately at
  `/tmp/capdep-live-baseline/mail-only-daemon.yaml`; it did not solve the guard
  failure and is **not** the active config. Do not repeat it without new evidence.

Metadata-only reports (including failures and synthetic numeric metrics):
`docs/evidence/email-daily-use-2026-10-07/runtime-unblocking/`.
Original private logs are in `/tmp/capdep-live-*.log`,
`/tmp/capdep-prefill-{2048,512}.log`, `/tmp/capdep-model-scoped-guard.log` and the
existing resource-guard log. Do not dump provider stderr, raw daemon.state,
session.list/history, legacy audit payloads, mail, or credentials into tool output.
Use explicit allowlists and validate unexpected keys/types for any new exporter.

## Verification and ownership

138 focused backend tests passed, exit 0, 30.86 seconds:
`/tmp/capdep-live-combined-backend.log`. This includes socket email acceptance,
serialized SMTP, source/recovery, recorder, routing, MLX, IMAP, retention and code
identity tests. The sandbox emitted a Metal atexit warning after tests passed;
actual guarded model inference ran separately outside the sandbox. An earlier
routing run had 3 expected-rule failures; tests now retain the earlier visibility
refusal AND independently test the original actual untrusted-egress boundary.
Scoped Pyright: 0 errors/warnings in `/tmp/capdep-live-all-pyright.log`.
Scoped Ruff and whitespace passed except a final import ordering issue, then
fixed by Ruff in `tests/test_email_reply_contract.py`. No broad full-suite claim.

Owned changed files (all were clean in the initial baseline, except new files):

- scripts: `inspect-mail-retention.py` (new), `test-supervised-mail-live.py`,
  `test-email-mvp-gui.py`, `test-email-mvp-local-model.py`.
- source: `agent/tool_selection.py`, `daemon/handlers.py`, `llm/mlx_client.py`,
  `mcp_servers/imap.py`, `tools/native/email.py`, `tools/native/mail_summary.py`
  under `src/capabledeputy/`.
- tests: `test_daemon_state_queries.py`, `test_email_daily_recovery.py`,
  `test_email_mvp_acceptance.py`, `test_email_reply_contract.py`,
  `test_imap_server.py`, `test_mlx_client.py`,
  `test_supervised_mail_live_recorder.py`, `test_supervised_mail_routing.py`,
  `test_mail_retention_inventory.py` (new).
- this handoff and runtime-unblocking metadata evidence directory.

No commit made by this lane yet. Review and commit only these owned changes.
Native sibling commits in shared checkout: `d1912d4` semantic renderer/dependencies,
`8589346` conversation/session drafts/follow behavior. Native sibling handoff:
`docs/handoffs/native-chat-ux-20261007.md`. Native source editing belongs to that
lane. Its 81-test pass predates small follow-ups; product build/native interaction
and combined email GUI gates remain pending. This lane changed GUI harness
expectations to semantic heading and TextKit paragraph newline boundaries; those
new expectations are **not yet native-verified**.

## Fresh continuation next steps

Coordinate with coordinator `01a114f8-2ee0-7443-9d4c-064cbc759bf5` and the fresh
native continuation. Prior sibling `01a11572-c063-74e3-ac1a-34619292f83f` has yielded.
The live read window is released: native may build/test now, but future model
inference requires a newly coordinated exclusive memory window.

1. Review owned source changes, final audit numeric metadata, guard timings and
   current host pressure. Determine why actual production inference still fails
   when the guarded synthetic workload succeeds. Keep the guard intact. Do not
   claim prefill tuning solved it. No smaller cached model was found under the
   inspected Models directory (only 8B/20B/24B/30B); broader cache search was not
   performed. No download/cloud fallback is authorized by this handoff.
2. Recheck idle state privately; source fingerprint must match tested disk bytes.
   `.venv/bin/python scripts/inspect-mail-retention.py` may require sandbox
   escalation for SQLite WAL shared-memory access. It does not render content.
   Use recorder no-read preflight with a NEW mode-0600 report path and actual
   startup Git SHA; native-only Git drift must be recorded explicitly.
3. After a demonstrated resource fix, retry bounded strict-dual local inference
   with one account and no send grant. Record real source/topic/date/coverage
   checks privately; returned audit commitments alone are not planner blindness
   or factual review. Current live evidence has no completed summary.
4. Prepare a real complete source-bound plain-text reply and native exact review,
   optionally self-addressed to the configured account. No draft/review was
   prepared in this lane. Never transmit without exact human approval through
   CapDep. Distinguish SMTP acceptance from independently observed inbox receipt.
5. Run combined native email regression after UX changes with
   `.venv/bin/python scripts/test-email-mvp-gui.py --artifacts <new-empty-dir>`
   (accepted, failed_before_submission, outcome_unknown; synthetic only), plus
   actual product/native gates supplied by the native lane. Coordinate builds.
6. Update #479 and readiness docs, commit scoped owned hunks with final tests.
   Seven elapsed days remain external; Day 1 requires a completed real supervised
   workflow. No approval/elapsed-day dependency should delay preparatory gates.

Exact-send approval, factual human source review, independently observed inbox
receipt, actual provider-revocation behavior, safe resource operation and seven
elapsed days remain distinct. Broad #454/#464 stay open; no policy floor was
weakened and no broad certification is claimed.
