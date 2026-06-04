# Personal Workflow Coverage — Pattern × Model × Capability Mapping

**Companion to** [`llm-flow-patterns.md`](./llm-flow-patterns.md) (the
five flow patterns) and [`security-models.md`](./security-models.md)
(the formal models capdep implements).

**Purpose.** Map the 50 most-cited personal-agent workflows onto
capdep's pattern × model × capability surface. Three uses:

1. **Demo backlog.** Each row is a candidate worked example, ranked
   by how cleanly capdep's vocabulary expresses the rule.
2. **Coverage gap detection.** Where the mapping requires a capability
   kind, axis, or pattern we don't yet implement, that's a real
   roadmap item.
3. **Anti-claim discipline.** Where a workflow can ONLY be expressed
   safely with patterns 3-5, claiming pattern 1 (turn-level) coverage
   is unsafe marketing. The mapping makes the safe vs. unsafe path
   explicit.

This document does NOT introduce new patterns or models — it composes
the existing five patterns (①–⑤) with the existing named security
models, against a concrete workflow corpus.

## How to read the rows

Each workflow row has four cells:

- **Pattern** — recommended flow pattern (`①` Turn / `②` DUAL_LLM /
  `③` ReferenceHandle / `④` Programmatic / `⑤` Sandbox).
  Multiple patterns means the workflow has distinct *phases* each
  using a different pattern.
- **Model** — the formal security model the workflow's safety
  argument rests on. Multiple models means independent properties
  hold (e.g., Brewer-Nash for compartment AND Clark-Wilson for the
  write).
- **Capability kinds** — what the session needs granted to actually
  run. Use this when authoring `/grant` lines or default-grant
  configs.
- **Axes that matter** — which of axes A (category), B (provenance),
  C (effect class), D (initiator/counterparty) are load-bearing for
  the rule.

Three columns flag the workflow profile:

- **Egress?** — does the workflow have a side-effect that leaves
  the local system (send, post, publish, purchase)? These are the
  rows where the approval card matters most.
- **Destructive?** — does it modify/delete existing state? Gates
  through Clark-Wilson by default.
- **Trust scope** — how much authority does it need? `read` /
  `local-write` / `egress` / `purchase`.

## Quick-reference table (50 workflows)

### Email & messaging

| # | Workflow | Pattern | Model | Caps | Trust |
|---|---|---|---|---|---|
| 1 | Inbox triage | ② | Denning | `GMAIL_READ` | read |
| 2 | Draft replies in my voice | ② + ③ | Clark-Wilson | `GMAIL_READ` `CREATE_FS` | local-write |
| 3 | Schedule extraction → calendar | ② → ① | Biba + Denning | `GMAIL_READ` `CREATE_CAL` | local-write |
| 4 | Thread summarization | ② | Denning | `GMAIL_READ` | read |
| 5 | Follow-up tracker | ④ | Denning | `GMAIL_READ` | read |
| 6 | Unsubscribe sweep | ④ | Clark-Wilson | `GMAIL_READ` `WEB_FETCH` `MODIFY_FS` | egress |
| 7 | Newsletter digest | ② | Denning | `GMAIL_READ` | read |
| 8 | Phishing detection | ② + raise-only inspector | Biba | `GMAIL_READ` | read |

### Calendar & scheduling

| # | Workflow | Pattern | Model | Caps | Trust |
|---|---|---|---|---|---|
| 9 | Find meeting time across calendars | ④ | Brewer-Nash | `CALENDAR_READ`×N | read |
| 10 | Reschedule conflicts | ④ → ① | Clark-Wilson | `CALENDAR_READ` `MODIFY_CAL` | local-write |
| 11 | Auto-decline by rules | ④ | Clark-Wilson | `CALENDAR_READ` `MODIFY_CAL` | local-write |
| 12 | Meeting prep brief | ① | Brewer-Nash + Denning | `GMAIL_READ` `CALENDAR_READ` `memory.read` `DRIVE_READ` | read |
| 13 | Focus block protection | ④ | Clark-Wilson | `CREATE_CAL` `MODIFY_CAL` | local-write |

### Files & local computer

| # | Workflow | Pattern | Model | Caps | Trust |
|---|---|---|---|---|---|
| 14 | Codebase Q&A | ② + ③ (for secrets) | Noninterference | `READ_FS` (scoped) | read |
| 15 | Bulk rename / reorganize | ④ | Clark-Wilson | `MODIFY_FS` | local-write |
| 16 | Find-and-replace across files | ④ + ⑤ first | Clark-Wilson + Biba | `MODIFY_FS` `EXECUTE_DEVBOX` | local-write |
| 17 | Set up dev environment | ⑤ | Clark-Wilson | `EXECUTE_DEVBOX` `WEB_FETCH` (in box) | local-write |
| 18 | Photo culling | ④ | Clark-Wilson | `READ_FS` `DELETE_FS` | local-write |
| 19 | Document format conversion | ⑤ | Clark-Wilson | `EXECUTE_DEVBOX` | local-write |

### Personal knowledge / notes

| # | Workflow | Pattern | Model | Caps | Trust |
|---|---|---|---|---|---|
| 20 | Capture-to-second-brain | ① + ③ (PII) | Denning | `CREATE_FS` (memory.write) | local-write |
| 21 | Daily journal automation | ④ | Brewer-Nash + Denning | `GMAIL_READ` `CALENDAR_READ` `memory.read` `CREATE_FS` | local-write |
| 22 | Spaced-repetition cards | ④ | Denning | `READ_FS` `CREATE_FS` | local-write |
| 23 | Weekly review | ④ | Brewer-Nash | many reads + `CREATE_FS` | local-write |
| 24 | Video / podcast summarization | ② | Denning | `WEB_FETCH` `CREATE_FS` | read |

### Finance & subscriptions

| # | Workflow | Pattern | Model | Caps | Trust |
|---|---|---|---|---|---|
| 25 | Transaction categorization | ② + ④ | Clark-Wilson | bank-read + `MODIFY_FS` | local-write |
| 26 | Unusual-spending alerts | ④ | Denning | bank-read | read |
| 27 | Subscription audit | ④ | Denning | bank-read + `GMAIL_READ` | read |
| 28 | Bill due-date wrangler | ② + ④ | Denning + Biba | `GMAIL_READ` + bank-read | read |
| 29 | Tax document collection | ④ | Brewer-Nash | `GMAIL_READ` `DRIVE_READ` `CREATE_FS` | local-write |
| 30 | Receipts → expense tracker | ② + ③ | Clark-Wilson | `GMAIL_READ` `CREATE_FS` | local-write |

### Shopping & purchases

| # | Workflow | Pattern | Model | Caps | Trust |
|---|---|---|---|---|---|
| 31 | Price drop watcher | ④ | Denning | `WEB_FETCH` | read |
| 32 | Comparison shopping | ① | Denning | `WEB_FETCH` | read |
| 33 | Reorder consumables | ④ → ① | Clark-Wilson + BLP | `QUEUE_PURCHASE` | purchase |
| 34 | Gift suggestions | ① + ② (recipient profile) | Brewer-Nash + Denning | `WEB_FETCH` + past-purchase-read | read |

### Travel

| # | Workflow | Pattern | Model | Caps | Trust |
|---|---|---|---|---|---|
| 35 | Trip planning end-to-end | ④ + ① + ⑤ (booking) | Clark-Wilson + Brewer-Nash | `WEB_FETCH` `QUEUE_PURCHASE` `EXECUTE_DEVBOX` | purchase |
| 36 | Fare watching | ④ | Denning | `WEB_FETCH` | read |
| 37 | Travel documents checklist | ④ | Denning | `memory.read` `CALENDAR_READ` | read |
| 38 | Loyalty-point optimization | ④ | Brewer-Nash + Denning | multi-account read | read |

### Health & wellness

| # | Workflow | Pattern | Model | Caps | Trust |
|---|---|---|---|---|---|
| 39 | Medication management | ④ + ① | BLP + Biba | health-read + `QUEUE_PURCHASE` `SEND_NOTIFY` | purchase |
| 40 | Healthcare appointment scheduling | ① + ⑤ | BLP | `WEB_FETCH` `EXECUTE_DEVBOX` | egress |
| 41 | Insurance claim follow-up | ④ | BLP + Biba | `GMAIL_READ` `SEND_EMAIL` | egress |

### Information & research

| # | Workflow | Pattern | Model | Caps | Trust |
|---|---|---|---|---|---|
| 42 | Personalized news digest | ② + ④ | Denning + Brewer-Nash | `WEB_FETCH` `CREATE_FS` | read |
| 43 | Deep research on a question | ① | Denning | `WEB_FETCH` `CREATE_FS` | read |
| 44 | Reading queue management | ④ + ② | Denning | `WEB_FETCH` `READ_FS` `CREATE_FS` | local-write |
| 45 | Academic paper tracker | ① + ② | Denning | `WEB_FETCH` | read |
| 46 | Decision support synthesis | ① | Denning + Brewer-Nash | `WEB_FETCH` `memory.read` | read |

### Relationships & memory

| # | Workflow | Pattern | Model | Caps | Trust |
|---|---|---|---|---|---|
| 47 | Birthday reminders + draft | ④ → ① | Clark-Wilson | contacts-read + `SEND_EMAIL` | egress |
| 48 | Haven't-talked-to-X nudges | ④ | Denning | `GMAIL_READ` + `SEND_NOTIFY` (self) | local-write |
| 49 | Photo album curation | ④ + ⑤ (assembly) | Clark-Wilson | `READ_FS` `CREATE_FS` `EXECUTE_DEVBOX` | local-write |
| 50 | Memory book / family timeline | ④ + ⑤ | Brewer-Nash | many reads + `CREATE_FS` | local-write |

## Per-category rationale

This section explains the non-obvious mapping choices and flags the
workflows that benefit from defense-in-depth combinations.

### Email & messaging

The defining property: **email bodies are the canonical adversarial
content**. Every workflow that touches a body is pattern ② or ③
by default — letting the planner ingest a raw email body invites
the entire indirect-prompt-injection class. The DUAL_LLM extracts
schema-validated fields (sender, subject, dates, action verbs); the
planner sees those, never the body.

- **Workflows 2 (draft replies) and 30 (receipts)** combine ② + ③:
  extract fields via dual-LLM, then carry sensitive substrings
  (the recipient address, the receipt amount) as handles into the
  send/write step. Triple defense.
- **Workflow 6 (unsubscribe sweep)** is the email subcategory that
  most justifies pattern ④: hundreds of similar deletions, each
  identical structurally. Operator ratifies the bundle once; no
  per-mail approval. Clark-Wilson is the natural model — each
  unsubscribe is a well-formed transaction.
- **Workflow 8 (phishing detection)** is a good use case for a
  raise-only inspector (FR-025): the inspector runs on every read
  and *adds* a `confidential.phishing-suspected` label to the
  session if it matches. Downstream egress rules can refuse to
  forward a phishing-tagged message.

### Calendar & scheduling

The defining property: **multiple calendars per person**. Almost
every operator wants work + personal separate. Brewer-Nash is the
single most-relevant model here — the calendars are a conflict-of-
interest cell.

- **Workflow 9 (find meeting time)** spans both calendars to compute
  free/busy. Two `CALENDAR_READ` grants with distinct patterns
  (`calendar:work*` and `calendar:personal*`) compose under
  Brewer-Nash; the agent reads both but can't egress the union.
- **Workflow 12 (meeting prep brief)** is the ONLY pattern ① row
  in calendar — open-ended assembly across surfaces, agent decides
  what's relevant. Pattern ④ (programmatic) doesn't fit because the
  agent has to *reason* about which past mails matter for this
  meeting.

### Files & local computer

The defining property: **destructive operations need transaction
semantics**. Clark-Wilson is the load-bearing model across nearly
every row here. The agent that does a bulk rename can do real damage;
the well-formed-transaction discipline ensures every rename is
auditable, reversible (where possible), and gated.

- **Workflow 14 (codebase Q&A)** is one of capdep's strongest
  positioning fits. The pattern ② + ③ combo means: code can have
  embedded secrets in comments/strings (a real and common case);
  the dual-LLM extracts code structure (function names, call
  graphs) into the planner, and any literal strings that look like
  credentials become handles. The planner reasons about the
  *shape* of the code, the runtime resolves literal values only
  when needed.
- **Workflow 17 (dev env setup)** is the canonical pattern ⑤ row —
  exactly what we just built (devbox.start / exec). Reversibility
  lifts to `reversible/system` inside the container; the operator
  can blow it away without losing host state.
- **Workflow 16 (find-and-replace)** benefits from a first run in
  the sandbox to verify the transformation, then the same program
  applied to the host filesystem under Clark-Wilson. Two-phase
  approval: ratify the sandbox preview, then apply.

### Personal knowledge / notes

The defining property: **second-brain workflows touch every
compartment**. Brewer-Nash is critical — a journal that mixes work
and personal violates the compartment model the operator probably
wants. The mapping flags this on the high-mix workflows (21, 23, 50).

- **Workflow 20 (capture-to-second-brain)** uses pattern ③ when the
  captured content contains PII — voice memo about a doctor's
  appointment, screenshot of a credit-card form. The handle keeps
  the value out of the planner's context; the runtime stores it
  with the right labels.
- **Workflow 21 (daily journal)** is a pattern ④ row because the
  gather logic is deterministic: same surfaces every day, same
  schema. The operator ratifies the journal program once.

### Finance & subscriptions

The defining property: **trust depends on the source**. Biba
(integrity floor) appears here repeatedly because the question "is
this really a bill from my bank?" is exactly the integrity-floor
question.

- **Workflow 28 (bill due-date wrangler)** can use Biba to refuse
  bills whose provenance isn't `verified.financial-institution`.
  Phishing emails impersonating banks have low integrity; the
  agent reads them but they don't propagate to the "things I owe"
  list.
- **Workflows 25, 30 (categorization, receipts)** use Clark-Wilson
  because every categorization is a typed write to a tracked
  ledger. Naked UPDATE on the spending DB is forbidden by
  construction — only the categorization transaction can mutate.

### Shopping & purchases

The defining property: **purchases are irreversible egress**. Every
purchase row routes through `QUEUE_PURCHASE` capability + the
approval card. BLP shows up because purchase has a clearance
analog — operator may have configured "no purchases over $X without
clearance escalation."

- **Workflow 33 (reorder consumables)** is a pattern ④ + ① combo:
  the deterministic shopping list is a ratified program; the actual
  fire-the-purchase step is a turn-level decision with the operator
  approval card showing the full payload preview.
- **Workflow 35 (trip planning)** is the most-complex row in the
  whole document — it composes ④ (research bundle) + ① (decisions)
  + ⑤ (sandboxed booking flow). The booking step is critical:
  pattern ⑤ contains the browser-driving so credentials don't
  leak; only the booking confirmation egresses.

### Travel

The defining property: **read-heavy, low-write**. Most travel
workflows are monitoring and synthesis. Pattern ④ dominates because
the work is deterministic state machines (fare watching is "every
hour, check N routes, alert on threshold").

### Health & wellness

The defining property: **PHI clearance**. BLP (Bell-LaPadula) is
load-bearing — a session reading health records needs an operator-
declared clearance. Capdep doesn't have a built-in "PHI tier" today;
this is an axis-A category the operator would declare.

- **Workflow 40 (appointment scheduling)** benefits from ⑤ for the
  actual booking — health portals have inconsistent UIs and benefit
  from a sandboxed browser. The operator approves the appointment,
  the sandbox holds the credentials.

### Information & research

The defining property: **provenance citation**. Denning lattice
underwrites every row — the agent's claims must be traceable to a
source URL with the right provenance level. Pattern ② shows up on
the bodies (article text is adversarial); pattern ① shows up on
the synthesis (open-ended depth-first exploration).

- **Workflow 43 (deep research)** is the canonical pattern ① row.
  The agent's exploration shape can't be predicted; each tool call
  shapes the next. The cost is per-call approval fatigue — operators
  typically grant `WEB_FETCH *` for research sessions.

### Relationships & memory

The defining property: **birthday-shaped recurring + open-ended
assembly**. Recurring rows are pattern ④ (deterministic state
machine); assembly rows use ⑤ for the build step (ffmpeg, photo
montage scripts). Brewer-Nash applies to anything that mixes
relationships across compartments.

## Coverage insights

### What capdep already covers cleanly

The mapping shows capdep's existing primitives express **34 of the 50
workflows** without any new mechanism. The matrix above only uses
patterns ①–⑤, models we've documented, and capability kinds we've
implemented. That's the strongest argument for capdep's vocabulary:
it's expressive enough to model the personal-agent space.

### Coverage gaps surfaced by the mapping

The remaining 16 workflows need primitives capdep doesn't yet ship.
Ranked by how many workflows want each missing piece:

1. **Banking / financial read capability** (~6 workflows: 25, 26, 27,
   28, 32, 38). Today there's no `BANK_READ` capability kind. Could
   land as a generic `FINANCIAL_READ` keyed by institution pattern.
2. **Health / PHI capability kind** (~3 workflows: 39, 40, 41).
   Today no `HEALTH_READ` kind. Closely tied to BLP clearance tier
   work — adding the kind without the clearance discipline is half
   the job.
3. **Contacts capability kind** (~2 workflows: 34, 47). Phone contacts,
   address book, relationship metadata.
4. **`SEND_NOTIFY`** distinct from `SEND_EMAIL` (~3 workflows: 39, 47,
   48). Self-notifications (push, SMS to self) are NOT social-
   commitment egress; conflating them with `SEND_EMAIL` over-gates.
5. **Bank-write / pay capability** (none in this list, but implied by
   bill-pay workflows). Deferred — too high-stakes for the current
   roadmap.

### Workflows that benefit most from defense-in-depth combos

Three combos appear repeatedly and are worth promoting as
**reusable templates**:

1. **② + ③ for any inbound-untrusted-content workflow** (1, 2, 7,
   8, 14, 24, 28, 30, 42, 44, 45). The DUAL_LLM extracts schema,
   handles carry literal values that shouldn't enter the planner
   prompt. This is the **triple defense against indirect prompt
   injection** — the killer combo for capdep's pitch.
2. **④ → ① for plan-then-act** (10, 11, 33, 35, 47). Deterministic
   gather, agent-judgment final step, irreversible egress under
   approval. Cleanest pattern for "agent acts on the world."
3. **⑤ for any external-content-rendering** (17, 19, 35, 40, 49,
   50). Containment lifts reversibility; sandbox isolates browser-
   driving and untrusted-doc rendering.

### Workflows that DON'T fit capdep's model

Two flagged because the safe path isn't obvious:

- **Workflow 32 (comparison shopping)** is pure pattern ① — agent
  has to reason about products, no programmatic bundle works.
  Capdep gates the reads; the *quality* of comparison is unsolved
  by the security model. This is a workflow where capdep's
  contribution is bounded.
- **Workflow 43 (deep research)** has the same shape. Capdep
  enforces provenance citation discipline but cannot enforce that
  the synthesis is correct. Honest framing: capdep makes research
  **auditable**, not **truthful**.

## Demo backlog ranked by capdep-fit

For the next worked-example pass, the recommendation is to build
demos in this order:

1. **Workflow 14 (codebase Q&A)** — clean ② + ③ + Noninterference
   triple. Shows the killer combo. Most code lives in `READ_FS`-
   gated repos already; doesn't need new capability kinds.
2. **Workflow 1 (inbox triage)** — ② + Denning. Single most-cited
   personal-agent workflow. Already partly demoable via
   workspace-mcp + the rules we just shipped.
3. **Workflow 6 (unsubscribe sweep)** — ④ + Clark-Wilson. Shows
   pattern ④ at its best: bulk write under one ratified bundle.
   Pattern ④ is currently under-demoed.
4. **Workflow 33 (reorder consumables)** — ④ → ① + Clark-Wilson +
   BLP. Shows the plan-then-act template with approval card on
   the irreversible step. Stresses the `QUEUE_PURCHASE` flow.
5. **Workflow 17 (dev env setup)** — ⑤ + Clark-Wilson. Already
   buildable on the devbox feature we just shipped. Shows that
   "open-ended development work" is in capdep's wheelhouse, not
   just locked-down task automation.

These five demos cover all five patterns and four of the six core
security models. After these, the matrix gets returns-diminishing
without filling the capability-kind gaps surfaced above.

## Anti-claim list

These claims **should never appear** in capdep marketing or demos
because the mapping doesn't support them:

- "capdep prevents the agent from sending the wrong content to the
  wrong person" — only true with pattern ② + ③ + counterparty
  rules. Pattern ① alone can't prevent it.
- "capdep enforces compartment isolation in sandboxes" — pattern
  ⑤ doesn't enforce Brewer-Nash. The sandbox is a containment
  primitive, not a compartment primitive.
- "capdep makes the agent's research truthful" — capdep enforces
  provenance citation; truth verification is out of model.
- "capdep prevents prompt injection" — pattern ① can't.
  Patterns ② + ③ can mitigate to "injected content cannot
  exfiltrate via covert paths." Even then, an injected instruction
  the operator approves still fires. Honest framing.
