# Personal Workflow Policy Cookbook

**Companion to**
[`personal-workflow-coverage.md`](./personal-workflow-coverage.md)
(the 50 workflows + pattern × model mapping) and
[`security-models.md`](./security-models.md) (the formal-model
yardstick).

**Purpose.** That mapping said *which pattern + model + capabilities*
each workflow uses. This document answers the operational question:
**where in the workflow does the operator actually get prompted, what
gets auto-allowed, what should never be allowed, and how do we keep
the prompt budget under control?**

If the coverage doc is "what can capdep express?", this one is "how
do you author the policy without drowning the operator in approval
cards?"

## 1. The approval economy

Every approval prompt costs operator attention. Three failure modes
to avoid:

- **Too many prompts → fatigue → rubber-stamping.** The operator
  starts hitting "approve" without reading. The system is now less
  safe than auto-allow would have been, because the audit trail
  *looks* approved.
- **Too few prompts → unsafe defaults.** The agent does something
  irreversible the operator wouldn't have approved.
- **Prompts at the wrong moment → useless.** "Approve `SEND_EMAIL
  to spouse@x.com` with payload of 4 KiB?" without the operator
  being able to see the actual content is theater.

The goal: **prompt where it would actually change the operator's
mind**, auto-allow everywhere else, deny outright where no operator
should be approving.

## 2. The minimization toolkit capdep ships

These are the mechanisms in capdep's existing surface — use them
before reaching for new policy.

| Mechanism | What it buys you | Where to use it |
|---|---|---|
| **Standing capabilities** (`/grant`) | Zero prompts for everything within a granted pattern | All read kinds (GMAIL_READ, READ_FS, etc.) — auto-grant `*` is usually fine |
| **Pattern ④ Programmatic** | One approval covers an N-step ratified bundle | Recurring deterministic workflows (nightly backup, unsubscribe sweep) |
| **FR-034 optimistic auto** | Reversible/system + non-egressing → AUTO | Most local file writes, draft creation |
| **Relationship groups** | Counterparty membership → expected, simpler prompt | Send to family, work team, known vendors |
| **Time-bound capabilities** (`expires_at`) | Wider authority for a finite window | Initial setup phases, debugging sessions |
| **Rate-limited capabilities** | N uses per window then prompt | Purchases, mass sends, destructive ops |
| **Per-server default labels** | Inherit compartment from source automatically | Inbound from gmail → `confidential.personal` |
| **`destructive_kind` gate** | Modify/delete need explicit `allows_destructive=True` | DELETE_FS, MODIFY_CAL |
| **Bundle dry-run** | Operator sees full plan in one card before any step fires | Trip planning, refactor, batch operations |
| **Recovery steps (FR-026)** | One-click resume from common denies | Path not in scope, missing capability |
| **Envelope dial** | Per-session "conservative / balanced / aggressive" | Risk-tolerance per task |
| **Approval queue grouping** | Sibling actions merged into one card | Batch sends to same counterparty |

The discipline: when tempted to add a new prompt, ask first whether
one of these mechanisms already covers it.

## 3. The six approval profiles

The 50 workflows cluster into six profiles. Each profile has a
**uniform approval policy** — author the rule once, apply to every
workflow in the profile.

### Profile A — Pure read, no egress (20 workflows)

**Workflows:** 1, 4, 5, 7, 8, 9, 12, 14, 22, 23, 24, 26, 27, 31, 32,
36, 42, 43, 44, 45, 46, 48.

**Default outcome:** **AUTO.** Reads to operator-curated sources
don't need per-call approval.

**Standing capabilities to grant at session start:**
```
/grant GMAIL_READ *
/grant CALENDAR_READ *
/grant DRIVE_READ *
/grant WEB_FETCH *
/grant READ_FS /home/marc/Projects/*    # narrow paths only
```

**What still prompts:**
- A read that would carry a label requiring egress approval *if
  it later participates in a send* — but the prompt fires at the
  send, not the read.
- A read that exceeds a rate limit — defense against aggregation
  attacks (rare in practice).

**Threats and mitigations:**
- *Prompt injection via read content.* Mitigation: pattern ②
  DUAL_LLM is the default flow pattern for adversarial sources
  (Gmail bodies, web pages). Configured per upstream server.
- *Exfiltration via summarization* — agent reads sensitive
  content, includes it verbatim in a later answer the operator
  reads, the operator pastes it elsewhere. Mitigation: pattern ③
  ReferenceHandle for genuinely-sensitive substrings (account
  numbers, passwords, names) so the value never enters the
  planner's prompt.

**What NEVER auto-allows in this profile:**
- Read of a path/source the operator hasn't granted. Fail-closed.
- Reads that compose into an egress action — those get prompted
  at the egress, not the read.

### Profile B — Local-write, no egress (14 workflows)

**Workflows:** 2, 3, 13, 15, 17, 18, 19, 20, 21, 25, 29, 30, 37,
49, 50.

**Default outcome:** **AUTO for `CREATE_FS`**, **SUGGEST for
`MODIFY_FS`**, **REQUIRE_APPROVAL for `DELETE_FS`** against user-
data paths.

**Standing capabilities to grant:**
```
/grant CREATE_FS /home/marc/Projects/*           # auto
/grant CREATE_FS /home/marc/.local/state/capdep/*
/grant MODIFY_FS /home/marc/Projects/*           # auto via FR-034
/grant CREATE_CAL *                              # personal calendar
```

**What prompts:**
- `DELETE_FS` against user data — always. Even one approval per
  delete is cheap; the cost of a wrong delete is high.
- `MODIFY_FS` against `.git/`, `~/.ssh/`, `~/.config/` — these
  paths have shared semantics with other tools; operator should
  see the diff.
- `MODIFY_CAL` on shared calendars (work calendar, family
  calendar) — affects others' time, not just the operator's.

**Threats and mitigations:**
- *Bulk-rename hits the wrong dir.* Mitigation: Clark-Wilson
  transactional logging surfaces the full list before commit;
  pattern ⑤ sandbox first run for any destructive bulk op
  (workflow 16).
- *Agent corrupts a config file.* Mitigation: narrow capability
  patterns; never grant `MODIFY_FS /home/marc/*` (too broad).
- *Calendar pollution.* Mitigation: separate `CREATE_CAL
  personal*` from `MODIFY_CAL work*`; only personal is auto.

**What NEVER auto-allows in this profile:**
- Writes to `~/.ssh/`, `~/.gnupg/`, `~/.config/git/`, any path
  with credential or signing material.
- Writes that change file ownership or executable bit.
- Writes to paths outside the granted pattern (fail-closed).

### Profile C — Outbound egress, irreversible (10 workflows)

**Workflows:** 6, 10 (cross-attendee impact), 11 (cross-attendee
impact), 33, 35, 39, 40, 41, 47.

**Default outcome:** **REQUIRE_APPROVAL** every time, with the full
recipient + payload preview.

**Standing capabilities to grant:** None for the egress kinds.
Approval is the gate.

**What prompts:**
- Every single send, post, purchase, or message that leaves the
  local box. Per Constitution: irreversible egress + social
  commitment = always approve.

**How to minimize approval count without weakening safety:**

1. **Relationship groups for recurring counterparties.** A `SEND_EMAIL`
   to spouse@... still prompts, but the approval card surfaces
   "spouse@... (family group, ratified)" so the operator's eye
   confirms recipient in 1 second instead of 10.
2. **Bundle similar actions.** N unsubscribes from one batch fire
   through a single bundle approval — operator approves the
   *unsubscribe program* (a hash of the action template), not each
   instance.
3. **Pattern ④ for ratified shopping lists.** `reorder.consumables`
   is a deterministic program; ratify the *list* once, the actual
   QUEUE_PURCHASE prompt shows just "approve list with these N
   items at total $X."
4. **Rate limits per relationship.** `SEND_EMAIL spouse@... rate
   3/hour` — past 3, prompts say "this is the 4th email to spouse
   in the last hour, continue?" Catches runaway loops.

**Threats and mitigations:**
- *Wrong recipient (typo, lookup error).* Mitigation: target_arg
  matching against capability pattern; counterparty rule shows
  match in approval card.
- *Wrong content (right recipient, wrong payload).* Mitigation:
  full body preview in approval card; pattern ③ handles for
  sensitive substrings so operator sees `pay($amount,
  ref:card_4f3a)` not the raw card number.
- *Time-pressure manipulation.* Mitigation: friction confirmation
  for high-stakes actions (typed confirmation, not single click).
- *Late-night autonomous loops.* Mitigation: `axis_d.time_window`
  rules — `SEND_EMAIL` outside business hours falls to
  REQUIRE_APPROVAL even when the counterparty rule would
  otherwise SUGGEST.

**What NEVER auto-allows in this profile:**
- Send to an unrecognized counterparty. Always prompts.
- Purchase above `max_amount`. Hard deny — not even approval; the
  cap must be widened first.
- Send carrying a label the rule denies (e.g., `confidential.financial`
  to a family-group recipient).

### Profile D — Sandboxed work (6 workflows)

**Workflows:** 16 (first run), 17, 19, 40 (booking sandbox), 49
(assembly), 50 (assembly).

**Default outcome:** **AUTO inside the sandbox** (containment lifts
reversibility to system); **REQUIRE_APPROVAL for anything that
crosses the sandbox boundary.**

**Standing capabilities to grant:**
```
/grant EXECUTE_DEVBOX py-dev         # or whichever specs you've declared
/grant EXECUTE_DEVBOX node-dev
```

**What prompts:**
- Promotion of a sandbox artifact to the host (copy out, install
  to host PATH, push to a remote).
- Sandbox network access if the operator declared the spec
  `network: none`. Default specs allow bridge for setup; some
  workflows (#16 destructive find-replace first run) should
  explicitly run in a `network: none` spec.
- Cross-session sandbox sharing — never auto, always prompt.

**Threats and mitigations:**
- *Data smuggled out via sandbox network.* Mitigation: declare
  spec with `network: none` for any session carrying
  `confidential.*` labels; demote-to-no-egress when a sensitive
  label lands (deferred feature).
- *Agent escapes the container.* Mitigation: capdep's hardening
  (--cap-drop=ALL, unprivileged uid); defense-in-depth via host
  kernel is out of scope (see anti-claims).
- *Workspace accumulates secrets.* Mitigation: `capdep
  maintenance workspaces --apply` for cleanup; `purge_workspace`
  on `devbox.stop` when session was sensitive.

**What NEVER auto-allows in this profile:**
- Mount of a host path the operator hasn't declared in the spec.
- Promotion of a sandbox artifact carrying any
  `confidential.compartment` label that the host session doesn't
  hold.
- Network access against an allowlist-deny policy (when configured).

### Profile E — Plan-then-act (5 workflows)

**Workflows:** 33, 35, 47, plus the act phase of 10, 11.

**Default outcome:** **AUTO for the planning phase**,
**REQUIRE_APPROVAL for the act phase** with the full plan shown.

**Standing capabilities to grant:** Profile A reads (since
planning is research-shaped) plus the act-phase capability gated
by approval.

**The cleanest pattern:** Pattern ④ Programmatic. The agent runs
the deterministic plan, produces a structured "what I would do"
artifact, the operator sees ONE approval card with the plan
preview, ratifies, the program executes.

**Example flow — workflow 33 (reorder consumables):**

1. Agent reads spending/usage data → no prompt (Profile A).
2. Agent computes shopping list (deterministic from inventory
   rules) → no prompt.
3. Agent presents bundle: "order 6 items, total $87, from these
   3 vendors." → ONE approval card, full preview.
4. Operator approves → all 3 purchases fire under the bundle's
   ratified hash.

**Threats and mitigations:**
- *Planning poisons the act phase.* Mitigation: pattern ③ handles
  for any values that influence the act (recipient address, dollar
  amount) — operator approval card shows the values the runtime
  will substitute, not what the planner could have manipulated.
- *Operator approves the plan but circumstances change before
  execution.* Mitigation: bundle execution captures a snapshot;
  TTL on the bundle so a stale approval can't fire next month.
- *Plan looks reasonable but contains one bad item.* Mitigation:
  the approval card itemizes — operator can reject individual
  items, not just the whole bundle.

**What NEVER auto-allows in this profile:**
- A planning phase that emits a "fire-and-forget" act without
  showing the operator. Plans must always produce an artifact.
- Execution of a bundle whose ratified hash is stale (TTL fires).

### Profile F — High-sensitivity reads (8 workflows)

**Workflows:** 25, 26, 27, 28, 29, 30 (financial), 39, 40, 41
(health).

**Default outcome:** **REQUIRE_APPROVAL on first read per session**,
**AUTO thereafter under the same clearance**.

**Standing capabilities:** None automatic; require explicit grant
each session.

**The clearance discipline:** When the session is created, the
operator declares its clearance tier (`personal-finance` for the
bank workflows, `phi` for the health ones). Reads against that
tier are then auto-allowed; reads above tier fail-closed (BLP no-
read-up).

**What prompts:**
- First time the session attempts to read financial/health data
  in its lifetime — confirms the operator intended this session
  to have that tier.
- Any egress that would carry a label from this tier — always
  prompts, never auto, regardless of relationship group.
- Any cross-compartment movement (financial → personal, health →
  share-with-family) — declassification action, fully gated.

**Threats and mitigations:**
- *Aggregation attack — many small reads sum to disclosure.*
  Mitigation: rate-limited capabilities; raise-only inspector that
  bumps session sensitivity tier after N reads in a window.
- *PHI leakage via summary.* Mitigation: pattern ③ handles for
  patient IDs, dates of birth, addresses; planner cannot include
  them in outputs.
- *Forwarding financial summary to wrong recipient.* Mitigation:
  Brewer-Nash conflict rule — `personal-finance` compartment and
  `social` compartment are mutually exclusive within one egress
  action. Forces declassification through an explicit gate.

**What NEVER auto-allows in this profile:**
- Egress carrying any financial or health label without explicit
  declassification approval.
- Cross-tier movement (PHI → public, financial → family).
- Bulk export of records.

## 4. The irrevocable boundary — what NEVER auto-allows

Regardless of which profile a workflow lives in, these actions MUST
prompt or hard-deny. Encode them as DENY rules with no narrowing.

1. **Send to an unrecognized counterparty.** No relationship group
   match, no prior approval. Always prompts.
2. **Purchase above `max_amount`.** Hard deny until the cap is
   widened by a separate operator action.
3. **Modify shared infrastructure.** SSH keys, GPG keys, sudo
   config, systemd units, cron entries — never auto.
4. **Delete user data** that isn't in a designated scratch/sandbox
   path. Always prompts.
5. **Cross-compartment declassification.** A label moving from
   `confidential.X` to a destination not carrying `X` — always
   gated, never auto.
6. **First-time action of a new effect class for this session.**
   Even if the capability is granted, the first use of a kind
   should prompt — confirms the operator intended this session to
   exercise that authority.
7. **Any action carrying `social_commitment=True`.** Sends,
   public posts, signed messages — always show the recipient and
   payload.
8. **Any action whose reversibility resolves to `irreversible /
   external`.** External means a third party can refuse to undo;
   that's an approval moment by definition.
9. **Use of a credential that the agent doesn't otherwise hold.**
   Reading from a vault into a tool call — gates at the vault read.
10. **Action on behalf of a different principal.** Delegation
    chain crossings.

## 5. The safe-to-auto boundary — what CAN always auto

These actions are safe to auto under FR-034 optimistic-auto and the
patterns above. The reversibility + non-egress combination is the
discipline.

1. **Any read of an operator-granted source.** Reads can leak via
   summary, but the leak fires at the *next* action, which itself
   gets gated.
2. **Local file writes inside the granted pattern, when the action
   is `CREATE_FS`.** Creating a new file in a known scope is
   reversible by deletion.
3. **Draft creation (email, document, calendar event).** Drafts
   are non-egressing by definition; promotion to send goes through
   the egress gate.
4. **Sandbox internal operations.** Containment lifts
   reversibility to `system`; AUTO is the default.
5. **Idempotent re-runs of an already-approved bundle.** Same
   inputs, same hash, same approval — no need to re-prompt.
6. **Append-only writes to operator-owned logs.** Audit log,
   personal journal append, capture-to-second-brain.
7. **Read-only synthesis output that stays in session.** Summary
   shown to the operator; no egress; no destructive write.

## 6. Threat catalogue and where each is addressed

| # | Threat | Where it shows up | Capdep mitigation |
|---|---|---|---|
| T1 | Indirect prompt injection from read content | Profile A (any read of adversarial source) | Pattern ② DUAL_LLM by default; ③ handles for sensitive substrings; pattern ① is explicitly unsafe for adversarial sources |
| T2 | Wrong recipient on egress | Profile C, F | `target_arg` pattern matching; counterparty rule shows match; approval card prominent recipient line |
| T3 | Wrong payload to right recipient | Profile C, E | Approval card full-body preview; pattern ③ handles for sensitive fields; structural schema validation |
| T4 | Aggregation attack — N low reads → high disclosure | Profile A, F | Rate-limited caps; raise-only inspector bumps tier after N reads |
| T5 | Cross-compartment leakage | Profile A → C, B → C | Brewer-Nash conflict rules; per-server `inherent_labels`; egress rules check label set |
| T6 | Destructive op on wrong target | Profile B, D | Narrow capability patterns; Clark-Wilson transaction logging; ⑤ sandbox first run for irreversible bulk ops |
| T7 | Confused-deputy via tool composition | Any multi-tool flow | Label propagation through composition; `target_arg` precision; type-aware schemas |
| T8 | Cost overrun on purchase | Profile C | `max_amount` on QUEUE_PURCHASE; approval card cost; rate limits per vendor |
| T9 | Acting on phishing as legitimate | Profile A → C (act on email) | Biba integrity floor; raise-only phishing inspector; refuse downstream egress on low-integrity inputs |
| T10 | Time-pressure / urgency manipulation | Profile C | Friction confirmation (typed confirm); `axis_d.time_window` rules; rate limits |
| T11 | Late-night runaway loop | Profile C | Time-window rules (off-hours → require approval); session rate caps |
| T12 | Sandbox data exfiltration | Profile D | `network: none` spec for sensitive sessions; demote-to-no-egress on label transition |
| T13 | Workspace accumulates secrets across sessions | Profile D | Idle reaper auto-stops containers; `capdep maintenance workspaces` cleanup; `purge_workspace` on stop |
| T14 | Stale-bundle execution | Profile E | TTL on ratified bundles; bundle hash includes timestamp |
| T15 | Operator rubber-stamps under fatigue | All profiles with prompts | Minimize prompt count via mechanisms in §2; design approval cards for fast evaluation |

## 7. Workflow-to-profile cross-reference

| # | Workflow | Profile | Notes |
|---|---|---|---|
| 1 | Inbox triage | A | ② default for Gmail server |
| 2 | Draft replies | B + C (on send) | Two-phase: draft auto, send approved |
| 3 | Schedule extraction → calendar | A → B | Pattern ②, then CREATE_CAL auto |
| 4 | Thread summarization | A | |
| 5 | Follow-up tracker | A | |
| 6 | Unsubscribe sweep | C | Pattern ④ bundles per batch |
| 7 | Newsletter digest | A | |
| 8 | Phishing detection | A + Biba inspector | |
| 9 | Find meeting time | A | Brewer-Nash on the compose |
| 10 | Reschedule conflicts | C (cross-attendee) | Each reschedule prompts |
| 11 | Auto-decline by rules | C if affects others; B if just personal | Split rule by calendar id |
| 12 | Meeting prep brief | A | |
| 13 | Focus block protection | B | Personal calendar only |
| 14 | Codebase Q&A | A | ② + ③ for embedded secrets |
| 15 | Bulk rename | B | DELETE elements prompt |
| 16 | Find-and-replace | D first, B after | Sandbox preview, then host apply |
| 17 | Dev env setup | D | AUTO inside devbox |
| 18 | Photo culling | B | DELETE prompts per batch |
| 19 | Document conversion | D | |
| 20 | Capture-to-second-brain | B | |
| 21 | Daily journal automation | A → B | ④ for the gather, AUTO write |
| 22 | Spaced-repetition cards | B | |
| 23 | Weekly review | A → B | |
| 24 | Video summarization | A | ② mandatory |
| 25 | Transaction categorization | F → B | First read prompts; cat is auto |
| 26 | Unusual spending alerts | F | Read-only, summary to operator |
| 27 | Subscription audit | F | |
| 28 | Bill due-date wrangler | F | Biba: refuse low-integrity bills |
| 29 | Tax document collection | F → B | Compartment-isolate `tax-2026` |
| 30 | Receipts → expense tracker | F → B | |
| 31 | Price drop watcher | A | |
| 32 | Comparison shopping | A | Honest: capdep doesn't enforce truth |
| 33 | Reorder consumables | E | Plan-then-act, ratified bundle |
| 34 | Gift suggestions | A | Brewer-Nash on recipient axis |
| 35 | Trip planning | E + D for booking | Research auto, bookings each prompt |
| 36 | Fare watching | A | |
| 37 | Travel documents checklist | A → B | |
| 38 | Loyalty-point optimization | F (account creds) | |
| 39 | Medication management | F + C | Refill prompts always |
| 40 | Healthcare appt scheduling | F + D + C | Sandbox the portal session |
| 41 | Insurance claim follow-up | F + C | |
| 42 | News digest | A | |
| 43 | Deep research | A | Pure pattern ①, capdep bounded value |
| 44 | Reading queue | A → B | |
| 45 | Academic paper tracker | A | |
| 46 | Decision support | A | |
| 47 | Birthday reminders | E | Plan auto, send approved |
| 48 | Haven't-talked-to-X nudges | A + B | Notification to self, no egress |
| 49 | Photo album curation | D + B | |
| 50 | Memory book / timeline | D + B | Brewer-Nash on compartments |

## 8. Approval-card design principles

Even with minimization, some prompts will fire. The card design is
where rubber-stamping is won or lost.

- **Recipient on its own line, large.** The single most-scanned
  field for egress decisions.
- **Payload preview, not just byte count.** "32 KiB" tells the
  operator nothing. The first 500 chars of the body do.
- **Counterparty status badge.** "Recognized: family group" vs.
  "Unknown: not in any group." One-glance assessment.
- **Diff for modifications.** Don't show the new file content;
  show what changed.
- **Cost prominent for purchases.** Currency + total, before any
  rationale text.
- **Recovery shortcuts.** When a deny happens, the card offers
  F1-F3 to grant the missing capability or widen the scope. The
  operator approves the FIX, not the action.
- **Friction proportional to stakes.** Click for read, click for
  reversible writes, typed confirmation for SEND_EMAIL, full
  payload type-confirm for purchases over $X.
- **Group siblings.** N similar actions in one card with a single
  approve-all + per-item reject toggles.
- **No multi-modal manipulation.** The card is text; no images
  that could be visual prompt injection vectors.

## 9. Putting it together — sample policy stanzas

For a session running "daily personal-life management" — most
common multi-workflow session type. Standing grants in `~/.config/
capabledeputy/auto_grants.yaml`:

```yaml
# Profile A — pure reads, auto
- kind: GMAIL_READ
  pattern: "*"
- kind: CALENDAR_READ
  pattern: "*"
- kind: DRIVE_READ
  pattern: "*"
- kind: WEB_FETCH
  pattern: "*"
- kind: READ_FS
  pattern: "/home/marc/Documents/*"
- kind: READ_FS
  pattern: "/home/marc/Projects/*"

# Profile B — local writes in scoped paths, auto via FR-034
- kind: CREATE_FS
  pattern: "/home/marc/Documents/inbox/*"
- kind: MODIFY_FS
  pattern: "/home/marc/Projects/journal/*"
- kind: CREATE_CAL
  pattern: "calendar:personal"

# Profile D — devbox specs the operator declared
- kind: EXECUTE_DEVBOX
  pattern: "py-dev"
- kind: EXECUTE_DEVBOX
  pattern: "node-dev"
```

Egress rules in `configs/rules.yaml` (companion to the existing
family/work-team rules):

```yaml
- rule_id: family-personal-email-suggest          # already shipped
- rule_id: work-team-email-suggest                # already shipped
- rule_id: purchases-under-threshold-auto
  when:
    axis_c:
      effect_class: queue_purchase
    axis_d:
      reversibility_degree: reversible
  outcome: auto
  rationale: small reversible purchases (returnable consumables) auto
  # Plus a cap-level max_amount on the QUEUE_PURCHASE grant
- rule_id: send-after-hours-require-approval
  when:
    axis_c:
      effect_class: send_email
    axis_d:
      time_window: [22, 6]
  outcome: require-approval
  rationale: night-time sends fall to manual approval even for known counterparties
- rule_id: phi-egress-deny
  when:
    axis_a:
      category: phi
    axis_c:
      effect_class: send_email
  outcome: deny
  rationale: PHI never egresses without explicit declassification override
```

The combination: Profile A + B is fully auto (no prompts after the
standing grants), Profile C prompts on every send with a relationship-
group-enriched card, Profile D auto inside the sandbox with prompts
only on promotion, Profile E one bundle approval per plan, Profile F
gates on first read + every egress.

A typical day for this session profile: **~3-5 approval cards** —
one or two egress sends, a purchase or two, maybe a bundle approval
for a trip plan. The 1000s of reads, drafts, and local-file
operations fire silently.

## 10. The discipline going forward

When adding a new workflow or feature, walk this checklist before
shipping:

1. Which profile (A–F) does it belong to?
2. What standing capabilities does the profile already cover?
3. What prompts SHOULD fire that aren't covered by the profile's
   default outcome? Add a rule.
4. What threats (T1–T15) apply? Confirm each is mitigated.
5. What's the prompt count for a typical execution? If >5, find a
   minimization mechanism from §2.
6. Does the workflow cross profiles (e.g., A→C plan-then-act)? If
   so, the act phase gets the strictest profile's discipline.
7. Is there an irrevocable-boundary action (§4)? Confirm it gates,
   not auto-allows.
8. Does the approval-card design match §8's principles?

The goal is: the operator's daily experience converges on
approving only what genuinely warrants their judgment, while
capdep handles the high-frequency low-risk work invisibly — and
when something does go wrong, the audit trail shows the operator
saw the right card at the right moment.
