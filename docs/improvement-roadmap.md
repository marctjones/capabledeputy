# Improvement Roadmap — Security Hardening + Approval Reduction

> **Historical snapshot.** This backlog has been superseded by the canonical
> roadmap in `../ROADMAP.md` and the sequencing companion in
> `implementation-plan.md`. Keep this file as rationale/history for the
> security-hardening and approval-reduction arc; do not use it as the current
> scheduling source of truth.

**Companion to**
[`personal-workflow-policy-cookbook.md`](./personal-workflow-policy-cookbook.md)
(the policy authoring discipline) and
[`security-models.md`](./security-models.md) (the model lineage).

**Purpose.** A focused, opinionated backlog of improvements to
capdep's current implementation. Two axes:

- **Security improvements** — close real or potential gaps in the
  existing model. Each item names a concrete defect or weakness.
- **Approval-reduction enhancements** — let capdep do more work per
  approval card while strengthening (not weakening) the safety
  story.

Items are ranked within each section by ROI per amount of code +
how much the cookbook §9 daily-life worked example actually
benefits. This is the queue I'd pull from when scheduling the next
spec or worked example.

The discipline from
[`personal-workflow-coverage.md`](./personal-workflow-coverage.md):
each proposed item is checked against Constitution Principles I–VIII
and the "stay focused" criteria from the earlier strategy discussion.
Items that would dilute the focus or reinvent prior art are flagged
as **out-of-scope** at the bottom.

## Part 1 — Security improvements

Ranked by severity × ease-of-fix.

### 1. ⚠ Time-window predicate fails OPEN when `now_hour` is None

**Severity: HIGH. Found while authoring the after-hours rule.**

`src/capabledeputy/policy/decision_rules.py:124` — when a rule
declares `axis_d_time_window` but the caller doesn't supply
`now_hour`, the time-window check is **skipped entirely** and the
rule matches as if the time predicate weren't there.

Concretely: a rule that says "during 22:00–06:00, escalate to
require-approval" matches regardless of time when the dispatcher
omits `now_hour`. In the after-hours rule's case this is fail-CLOSED
by accident (the rule escalates, matching restricter intent). But
the same construction with a time-window AUTO rule would be a real
fail-open — that rule would AUTO regardless of time.

**Fix:** when `axis_d_time_window` is set and `now_hour is None`,
the predicate MUST return False (fail-closed per Principle VI). The
rule then doesn't fire; composition falls to whatever other rules
match (or the default SUGGEST). The dispatcher SHOULD always supply
`now_hour` — failing to is a caller bug worth surfacing.

**Effort:** ~10 lines. Add to RulePredicate.matches; add test that a
time-window rule does NOT fire when `now_hour is None`.

**Why it matters:** today's `send-after-hours-require-approval` is
fine, but a future rule like `daily-cron-window-auto` would silently
AUTO outside its declared window.

### 2. Engine-level fail-closed gate for `EXECUTE.devbox`

**Severity: MEDIUM. Defense-in-depth.**

The engine has a fail-closed gate for `EXECUTE.sandbox` when no
SandboxActuator port is wired (`engine.py:792`). The devbox commit
explicitly noted this gate is missing for `EXECUTE.devbox` — today
the tool layer prevents tool registration when no manager is wired,
which is a single layer of defense. The engine should fail-closed
too, mirroring the sandbox precedent.

**Fix:** parallel block in `engine._decide_impl` checking
`effect_class.startswith("execute.devbox")` against a
`devbox_manager_wired` parameter threaded from PolicyContext. Same
shape as the existing sandbox check.

**Effort:** ~30 lines + tests.

**Why it matters:** the threat is a misconfigured deployment where
the tool registration check passes (manager wired) but during a
turn the manager errors and silently returns no result. The engine-
level check would refuse the action with a typed reason.

### 3. Session-end teardown hook for devboxes

**Severity: MEDIUM. Operational hygiene.**

There's no "session ended" event in capdep. When the operator runs
`session.abort` or simply exits chat, devboxes for that session
stay running until the idle reaper fires (default 1 hour later) and
the workspace dir survives until the operator runs
`capdep maintenance workspaces --apply`.

**Fix:** emit a `SESSION_TERMINATED` event from `SessionGraph` on
abort / explicit end. `App` subscribes and calls
`devbox_manager.stop_session(session_id)`. Optional flag on
session-end whether to purge workspace.

**Effort:** ~50 lines + tests + a small protocol change (new event
type) — moderate because the session-end concept doesn't exist
today.

**Why it matters:** containers accumulate; on a multi-week-long
machine, leaked devboxes can hold non-trivial disk + memory.

### 4. Fail-closed when Pattern ② is configured but quarantined LLM is missing

**Severity: MEDIUM. Principle VI bug.**

A session can be spawned with `flow_pattern: pattern_2_dual_llm`
intent in its purpose, but if no `quarantined_llm` is wired on App,
the dispatcher silently falls through to single-LLM execution. The
operator's intent ("don't let adversarial content into the planner
prompt") is silently violated.

**Fix:** when a per-server or per-purpose pattern declares ②, the
tool client should refuse to dispatch a tool whose result would
require the quarantined LLM if none is wired. Typed error;
operator sees the misconfiguration.

**Effort:** ~40 lines + tests. The dispatcher already has the
quarantined LLM reference; add the precondition check at dispatch.

**Why it matters:** Pattern ② is one of capdep's killer combos for
the prompt-injection story. Silent fallback to ① is the worst
failure mode.

### 5. First-action-of-kind prompt

**Severity: LOW-MEDIUM. UX hardening.**

Cookbook §4 says the first time a session uses a new effect class,
the operator should see a prompt — confirming the session was
intended to exercise this authority. Today, a session with
`SEND_EMAIL *` granted by default sends without warning on the
first invocation.

**Fix:** add a `first_use_of_kind_seen` set to Session state.
`engine.decide()` returns SUGGEST (not AUTO) the first time a kind
fires, regardless of what the rules say. After approval, the kind
moves to whatever its standing rule says.

**Effort:** ~80 lines including persistence + tests + UX wiring.

**Why it matters:** catches misconfiguration. A session that was
accidentally granted `SEND_EMAIL` would prompt on first use rather
than silently sending.

### 6. Tamper-evident audit log

**Severity: LOW. Provenance hardening.**

`audit.jsonl` is append-only by convention but the file itself can
be edited or truncated. A compromise could rewrite the audit trail.

**Fix:** each event line includes `prev_hash` — SHA-256 of the
previous line. Audit verification walks the chain; any break
indicates tampering.

**Effort:** ~100 lines + tests + a verifier subcommand. Schema
change for existing events (back-compat: missing `prev_hash`
allowed on legacy lines).

**Why it matters:** SC-002 replay determinism depends on audit
integrity. Today integrity is "the file wasn't modified" by
operator discipline; the hash chain makes it cryptographic.

### 7. Quarantined-LLM output validator

**Severity: LOW. Defense-in-depth for ②.**

Pattern ② extracts schema-validated fields via the quarantined
LLM. If the quarantined LLM is itself compromised by a clever
injection in the source content, it could emit malicious values
that pass schema validation (e.g., a base64-encoded payload in a
`subject` field). Today the dispatcher trusts the schema.

**Fix:** declare per-field constraints beyond JSON-schema:
character allowlists, length caps, structural patterns. Reject any
extraction whose fields violate constraints; raise the session's
taint.

**Effort:** ~100 lines + the constraint DSL design. Worth a small
spec.

**Why it matters:** the dual-LLM model only holds when the
quarantined output is genuinely structured. Stronger structural
gates close the obvious bypass.

### 8. Capability grant CLI validates targets exist

**Severity: LOW. UX defect that affects safety.**

Today `/grant SEND_EMAIL spouse` succeeds and matches nothing
useful (looking for literal target "spouse"). Operator thinks they
granted authority; the grant is dead. Fail-quietly.

**Fix:** the grant CLI validates patterns against known target
shapes per kind. SEND_EMAIL expects an email-shaped pattern;
READ_FS expects an absolute path; WEB_FETCH expects a URL. Warn on
malformed.

**Effort:** ~50 lines + tests.

**Why it matters:** operator confidence. "I granted X" should mean
"X is grantable."

## Part 2 — Approval-reduction enhancements

Ranked by how much they reduce daily prompt count for the cookbook's
typical session, weighted by adoption ease.

### 1. Approval sibling-grouping

**Estimated daily prompt reduction: 20-40%.**

The cookbook §8 talks about grouping sibling actions. Today, three
sends to the same counterparty within 5 seconds produce three
cards. Operator approves each; the marginal cards have no new
information.

**Implementation:** the approval queue's de-duplication key
includes `(session_id, kind, target, payload_hash_window)`. Two
actions with the same key within N seconds merge into one card
with `approve all + per-item toggle`.

**Effort:** ~150 lines + UX changes. The queue and bundle
infrastructure exists; this is composing existing pieces.

**Why it matters:** the most painful daily friction. Replying to
three threads from the same person should be ONE approval, not
three.

### 2. Pattern ⑥ Audit-only / shadow mode

**Estimated daily prompt reduction (during adoption): 100%.**

Before flipping a session to enforce, run it in shadow for K turns.
The engine emits "WOULD HAVE PROMPTED" events; the operator reviews
the log, ratifies the rules, then flips to enforce.

**Implementation:** new flag on Session: `enforcement_mode: shadow |
strict`. In shadow mode, decide() returns AUTO regardless of
outcome but emits a `would_have_prompted` audit event. After the
operator reviews + ratifies, flip to strict.

**Effort:** ~200 lines + tests + a chat REPL command
`/enforce strict` / `/enforce shadow`.

**Why it matters:** the single biggest blocker to adoption. New
operators don't want their first hour with capdep to be 50
approval prompts.

### 3. Capability auto-narrowing from approval

**Estimated daily prompt reduction: 10-20% over time.**

When the operator approves `SEND_EMAIL spouse@example.com`, the
approval card offers a one-click "remember this recipient" that
adds spouse@example.com to the family relationship group. Next
time, the family rule fires; the card shows "recognized: family
group" with less friction.

**Implementation:** approval card decision shape extended with
`also_grant_pattern` or `also_add_to_group`. On approval with the
option set, the group/grant is modified atomically with the
decision.

**Effort:** ~100 lines + UX changes + a small data-model update.

**Why it matters:** the operator's repeated approvals converge on
policy. Each approval implicitly votes for "this is normal."

### 4. Per-session persona bootstrap UX

**Estimated daily prompt reduction: indirect — adoption enabler.**

Today the operator writes `/grant ...` lines or sets up a Purpose
file. The new `--purpose` flag (now wired via the cookbook's
purposes.yaml) is good but still requires the operator to
understand purposes.

**Implementation:** `capdep chat --persona personal` runs the
session with the `daily-life-management` purpose. `--persona
finance` uses the personal-finance purpose. Easy mode for common
configurations.

**Effort:** ~50 lines in the chat command + persona→purpose
mapping in config.

**Why it matters:** removes a layer of indirection. A new user
gets the cookbook's default policy with one flag.

### 5. Time-bound (`--ttl`) grants from chat REPL

**Estimated daily prompt reduction: 5-10%, situation-dependent.**

The Capability dataclass already supports `expires_at`. The
`/grant` chat command doesn't expose it. An operator who wants
"give the agent SEND_EMAIL authority for 1 hour while I set up the
trip planning" has to type the grant, do the work, then explicitly
revoke.

**Implementation:** `/grant SEND_EMAIL spouse@x.com --ttl 1h`
mints a Capability with `expires_at = now + 1h`. After expiry it
no longer matches; engine returns no-matching-capability.

**Effort:** ~40 lines. Parse the duration flag; pass to the
existing Capability.expiring_in constructor.

**Why it matters:** burst-mode work without permanent authority
expansion.

### 6. Rate-limit-as-friction (escalate-instead-of-deny)

**Estimated daily prompt reduction: -5% in normal use, but big in
adversarial: lets the operator catch runaway loops.**

Capability `rate_limit` today produces a hard deny on overflow.
Better: instead of denying, escalate the outcome to REQUIRE_APPROVAL
after the rate threshold. Lets the operator vouch mid-stream
(approve the 4th send) instead of losing the session to a deny.

**Implementation:** PolicyDecision adds a `rate_exceeded_escalation`
path. When the cap is rate-exceeded, return SUGGEST (not DENY)
unless the operator's risk dial is conservative.

**Effort:** ~80 lines. Touches engine.decide() + tests for the
existing rate-limit tests.

**Why it matters:** catches autonomous loops. The operator sees
"this is the 6th send in 5 minutes — continue?" instead of the
agent silently failing.

### 7. Default-decline-after-N-minutes for approval cards

**Estimated daily prompt reduction: 0%, but fixes UX bug.**

An approval card sitting in the queue forever blocks the agent.
After N minutes of operator non-response, default to DENY (or
SUGGEST → operator can still see it later); the agent gets a
typed "approval_timeout" error and can react (skip, retry later).

**Implementation:** ApprovalQueue grows a TTL per entry. Background
task or per-call check fires the default outcome after expiry.

**Effort:** ~80 lines + tests.

**Why it matters:** an agent stuck on a 3-day-old approval card is
a worse failure than a clean timeout.

### 8. Pattern ⑥ audit-only mode for individual rules

**Estimated daily prompt reduction: variable.**

Today each rule has an outcome (auto/suggest/require-approval/
deny). A "shadow rule" outcome would let an operator add a rule
without enforcing it — the engine logs what the rule WOULD have
done but doesn't compose it into the decision. Lets operators
A/B-test rules safely.

**Implementation:** new RuleOutcome `SHADOW`. Engine evaluates,
emits a `rule_shadowed` event, but excludes from the
most-restrictive composition.

**Effort:** ~50 lines + tests.

**Why it matters:** safer rule iteration. An operator can author a
narrow PHI-deny rule and test it for a week before flipping to
enforce.

## Part 3 — Discipline check: what's NOT on this list

These were considered and rejected because they don't reinforce
the existing model or would dilute focus:

- **eBPF / Tetragon / Cilium integration.** Already discussed: off-
  mission. capdep's contribution is the policy layer, not kernel-
  level enforcement. Run capdep under Tetragon if you want both —
  don't vendor it.
- **Adversarial-prompt detection / output classifiers.** NeMo /
  Cisco AI Defense territory. capdep deliberately assumes the LLM
  may be adversarial (Principle I) and gates at the action layer.
  Adding output classification adds layers without reinforcing the
  claim.
- **Differential privacy budget for label declassification.**
  Tempting but different problem (statistical leakage). The
  cookbook's aggregation-control gap is real but better addressed
  with rate-limited capabilities than DP.
- **Multi-tenant / multi-principal IFC.** DIFC (Myers/Liskov) per-
  principal labels are theoretically clean but capdep is single-
  user-oriented today. Adding per-principal label algebras before
  there's a real multi-principal use case is premature.
- **Cryptographic capability tokens (macaroons).** Compelling for
  cross-process capability passing but capdep is in-process today.
  Until there's a multi-daemon scenario, this is solving a future
  problem.
- **Approval card visual redesign / GUI.** Outside the scope of a
  terminal-first tool. A REPL with rich text + clickable links is
  the contract.

## Part 4 — Recommended next-deliverable bundle

If we ship one focused commit cycle, the highest-ROI bundle is:

**Security:** items 1 (time-window fail-open fix) + 2 (engine
devbox gate) + 4 (quarantined LLM fail-closed). These are real
correctness fixes; each ~30-50 lines + tests; together they
close three Principle VI bugs in one pass.

**Approval reduction:** item 1 (sibling grouping) + item 4
(persona bootstrap). Sibling grouping is the single biggest
daily-friction reducer; persona bootstrap is the adoption enabler.

Total estimated effort: ~600 lines + tests + small docs. One
commit cycle.

After that, the next deliverable is the **shadow-mode (item 2)**
plus the **demo backlog from the coverage doc** — pick a worked
example, run it in shadow for K turns, ratify the policy, flip to
strict. That converts the cookbook's prescription from "policy
doc" to "lived discipline" with an audit trail to prove it.

## Part 5 — Cross-reference

For each cookbook §4 irrevocable-boundary item (1–10), which
improvement above closes the current gap:

| Cookbook §4 item | Closest improvement |
|---|---|
| 1. Unrecognized counterparty send | Improvement P2.3 (auto-narrowing): the first approval expands the group; subsequent sends to that recipient are recognized. |
| 2. Purchase above cap | Already enforced via `max_amount`. No gap. |
| 3. Shared infra modification | Today: narrow capability pattern. Improvement P1.8 (target validation) hardens. |
| 4. User-data delete | Today: prompt. No gap. |
| 5. Cross-compartment declassification | Today: rule + override grant. Composable with improvement P2.6 (shadow rules) for safer iteration. |
| 6. First action of new effect class | **Gap.** Improvement P1.5 closes it. |
| 7. social_commitment=true | Today: rule. No gap. |
| 8. irreversible/external reversibility | Today: rule. No gap. |
| 9. Vault credential read | Today: handled if vault wired. Improvement P1.7 hardens the schema validation. |
| 10. Cross-principal delegation | Today: depth-limited delegation chain. Adequate. |

The honest gaps are at cookbook §4 items 1, 3, 6, 9. The above
improvements address all four.
