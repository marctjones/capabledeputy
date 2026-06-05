# Improvement Roadmap v2 — Sibling Backlog

**Companion to**
[`improvement-roadmap.md`](./improvement-roadmap.md) (the original
backlog, now fully shipped) and
[`personal-workflow-policy-cookbook.md`](./personal-workflow-policy-cookbook.md)
(the policy authoring discipline).

**Purpose.** v1 of the improvement roadmap is closed — all 16
items from Part 1 (security gaps) and Part 2 (approval economy)
are shipped. This document picks up the **sibling items**: things
mentioned in passing in commit messages, cookbook sections, or
review discussions that didn't make the original list but still
represent real value. None are existential gaps; all are bounded
in scope.

Items are ranked within each section by ROI per amount of code.
Effort estimates assume the codebase as of `da44d21`
(2026-06-04, 26 commits ahead of `origin/003-labeling-framework`).

This document is the queue for the **next** focused arc, not a
total spec. Items here may turn out to compose or split; the
estimates are ballpark.

## Part 1 — Operational hygiene gaps

These were called out as "Deferred" in commit messages but are
real follow-up work.

### 1. Daemon-shutdown teardown for live devboxes

**Severity: LOW. Operational hygiene.**

When the daemon stops, live devbox containers (and their
in-memory `_LiveDevbox` records) are abandoned. The idle reaper
(shipped in `c5dafd0`) eventually catches up, but containers can
sit running for the full reaper interval before being noticed.
On a clean daemon shutdown, every container should be torn down
immediately.

**Fix:** `App.shutdown` already exists (added in the devbox
commit for the idle-reaper task). Extend it to call
`devbox_manager.stop_session(sid)` for every live session before
returning. Wire `daemon.lifecycle.stop_daemon` to await
`App.shutdown` rather than just signaling the process.

**Effort:** ~30 lines + tests.

**Why it matters:** an operator running `capdep daemon stop`
expects "everything is cleaned up." Today there's a window where
containers leak past the daemon's death — surprising and
forensically annoying.

### 2. Cross-file audit chain verification

**Severity: LOW. Provenance completeness.**

`capdep audit verify` (cookbook P1.6, shipped `78b52c9`) walks
only the active `audit.jsonl`. Rotation preserves the hash chain
across files when `max_rotated >= 1` — the rotated files have a
valid chain — but the verifier doesn't follow it. An attacker
who tampers with a rotated archive (`audit.jsonl.1`,
`audit.jsonl.2`) goes undetected.

**Fix:** verifier accepts `--include-rotated` and walks the
files in chronological order (oldest archive → ... → archive.1 →
active), threading the expected `prev_hash` across the boundary.
First-line of `archive.N` must chain to last-line of
`archive.N+1`; first-line of active must chain to last-line of
`archive.1`.

**Effort:** ~80 lines + tests including a multi-file fixture.

**Why it matters:** completes the audit-integrity story.
Operators who set `max_rotated > 0` get the full forensic
guarantee instead of an active-only one.

### 3. `capdep maintenance audit` rotation policy

**Severity: LOW. Operator agency for retention.**

The existing audit writer rotates by size; operators can't
declare a TIME-based retention policy (e.g. "keep 30 days,
archive older to off-host"). `capdep maintenance audit` exists
as a placeholder in the `maintenance.py` docstring but isn't
implemented — the original maintenance commit explicitly
deferred it as a separate concern.

**Fix:**
- `capdep maintenance audit` reports total size, oldest event
  timestamp, line count.
- `capdep maintenance audit --truncate-before <ISO timestamp>`
  drops events older than the timestamp (writes the surviving
  tail to a new file; preserves chain integrity via a "genesis"
  marker on the new first line).
- `--archive <path>` moves rotated files to a target directory
  instead of deleting them (cron / rsync friendly).
- Hash-chain verification runs automatically before any
  destructive operation.

**Effort:** ~150 lines + tests.

**Why it matters:** unbounded audit log growth is a real
problem on long-running daemons. The hash chain only helps if
the file isn't multi-gigabyte.

## Part 2 — Approval-economy refinements

These came up in cookbook §§ 2, 6, 8 but weren't in the original
P2.* numbered list.

### 4. Per-counterparty reputation tier

**Severity: LOW. Approval-quality improvement.**

Today the family-personal-email rule fires SUGGEST for every
send to a recognized counterparty (cookbook §3 Profile C). After
N approved sends to the same recipient with zero issues, the
operator might reasonably want lighter friction — sender +
subject preview rather than full body, or auto-allow within a
budget.

**Fix:** new RPC `relationship_group.tier(group_id,
principal_id) → str` returning `unproven | well-tested |
trusted`. Tier promotion is operator-only (chat REPL
`/promote <group> <principal>`) but informed by per-counterparty
audit aggregates the chat REPL surfaces (`approved: 47 over 6
months; denied: 0`).

The approval card UX varies by tier:
- **unproven**: full body preview + recipient highlighting
- **well-tested**: subject + first 200 chars + recipient
- **trusted**: subject + recipient only (still REQUIRE_APPROVAL,
  one-click confirm)

**Effort:** ~200 lines including the RPC, chat REPL surface,
and approval-card variation logic.

**Why it matters:** cookbook §3 Profile C generates the most
operator friction in a daily-life session. Lighter cards for
the recipients that have proven safe is the most-requested
followup in informal feedback.

### 5. Bundle pre-flight cost estimate

**Severity: LOW. Plan-then-act UX improvement.**

Pattern ④ programmatic bundles get one approval card today
showing the list of items. For purchase bundles (reorder
consumables, trip planning) the operator wants to see the
**total cost** + the **per-item breakdown** before approving.

**Fix:** approval queue's `submit` accepts an optional
`cost_estimate: dict[str, Any]` field. Bundle approval cards
render it prominently above the per-item list: total in big
text, per-item subtotal in the table.

**Effort:** ~80 lines + UX changes.

**Why it matters:** reduces the gap between "approve this plan"
and "actually understand what I'm approving" for purchase
bundles.

### 6. Stale-bundle TTL

**Severity: LOW. Threat T14 mitigation.**

Cookbook §6 threat T14 (stale-bundle execution) is mitigated
"by convention" today — a ratified bundle has no expiry, so
something the operator approved last month could fire stale if
re-queued. Approval TTL (P2.7, shipped) handles per-card; this
handles per-bundle.

**Fix:** add `expires_at: datetime | None` to the bundle
record (same shape as approval expiry). Bundle dispatch
refuses execution if `expires_at` has passed; surfaces as a
typed `BundleExpiredError`. Default TTL 24 hours, configurable
via `CAPDEP_BUNDLE_TTL_SECONDS`.

**Effort:** ~60 lines + tests.

**Why it matters:** caught one real T14 scenario in cookbook
review; the convention isn't enough.

## Part 3 — Audit / observability polish

### 7. First-use prompt distinct UX in REPL

**Severity: LOW. UX polish.**

First-use prompts (cookbook §4 #6, shipped `74d4265`) currently
flow through the standard approval queue and look like any
other REQUIRE_APPROVAL. The operator can't visually distinguish
"first time you've ever used SEND_EMAIL in this session — is
that intended?" from "the family-personal-email-suggest rule
fired again."

**Fix:** approval card carries the rule name (`rule:
first-use-of-kind`); the chat REPL renders first-use cards with
a distinct banner ("⚠ first use of SEND_EMAIL in this session
— confirm intent") and a friendlier explainer.

**Effort:** ~40 lines.

**Why it matters:** first-use prompts ARE different from rule-
driven approvals — the operator should be able to tell.

### 8. Hubble-style live flow visualization

**Severity: LOW. Premium observability for advanced users.**

The audit log captures every decision; the chat REPL renders
recent activity inline. There's no live flow graph showing the
data flowing in real time (session reads from Gmail → calendar
→ memory → triggers SEND_EMAIL). Cookbook §10 anti-claim list
specifically warns against marketing this as enforcement, but
it's still a useful observability primitive.

**Fix:** new chat REPL command `/flow [seconds]` shows a Rich
live region with the recent flow graph (sources on the left,
sinks on the right, labels propagating). Static markup rendered
from audit events; no kernel-level data.

**Effort:** ~200 lines including Rich graph rendering.

**Why it matters:** operators describe "what is the agent
actually doing right now" as the question they most want to
answer mid-session. Today they read the audit log; this is the
visualization.

## Part 4 — Devbox + sandbox refinements

### 9. Per-spec network allowlist

**Severity: LOW. Closes a partial item.**

Cookbook §5 / `configs/upstream-isolated.example.yaml` declares
`allowed_hosts` for upstream MCP servers. The devbox spec has
`network: bridge | none` but no per-spec **allowlist** — a
spec with `network: bridge` allows arbitrary egress. Operators
who want "py-dev can talk to pypi.org and github.com but
nothing else" can't express it today.

**Fix:** add `allowed_egress_hosts: tuple[str, ...]` to
`PodmanRegionSpec`. When non-empty, the Podman `run -d`
command adds `--add-host` / nftables rules confining outbound
to the listed hosts. Pre-existing `network: none` still
overrides (no egress at all).

**Effort:** ~80 lines + integration test with a real Podman
network setup (the e2e test can use a real container if
`podman` is on PATH).

**Why it matters:** the cookbook's daily-life-management
session wants devboxes that can install dependencies but
can't exfiltrate. Today it's all-or-nothing.

### 10. Workspace size cap + reaper

**Severity: LOW. Operational hygiene.**

The devbox idle reaper (shipped `c5dafd0`) stops idle
containers but doesn't bound workspace size. A long-running
session whose agent writes large build artifacts to `/work`
can accumulate gigabytes silently.

**Fix:** `PodmanRegionSpec.max_workspace_bytes` (default 1
GiB). When the workspace exceeds the cap, the next `exec`
returns a typed `WorkspaceOverquotaError` and the chat REPL
surfaces "your devbox workspace is X GiB — clean up with
`devbox.stop --purge` or raise the cap in daemon.yaml."

**Effort:** ~60 lines + tests.

**Why it matters:** the maintenance CLI (also shipped
`c5dafd0`) lets operators free space MANUALLY but doesn't
PREVENT runaway accumulation in the first place.

## Part 5 — Spec-level work (bigger items)

These would warrant their own spec rather than just an
improvement-roadmap item. Listed here for visibility, not as a
near-term commitment.

### 11. Bundle ratification UI

Pattern ④ bundles are great in theory; in practice the operator
needs a way to AUTHOR a bundle interactively rather than
hand-writing JSON. A chat REPL flow:
- Run the agent in shadow mode
- After a successful turn, `/bundle` extracts the action list
- Operator names + signs the bundle
- Future invocations of that intent execute the ratified bundle
  directly

**Effort:** ~spec + ~500 lines.

### 12. Multi-principal IFC (DIFC labels)

Cookbook §6 anti-claim list explicitly defers multi-principal
labels as a future research item. Real use cases (family
session shared by spouses, work session shared with a delegate)
exist. The Myers/Liskov DLM model fits cleanly onto the
existing axes structure; the work is mostly UX (how does an
operator declare "owner: alice, reader: bob"?) plus per-action
principal resolution.

**Effort:** ~spec + ~1500 lines.

### 13. Capability templates + grant catalog

Today `/grant SEND_EMAIL spouse@x.com --ttl 3600 --rate 5/60`
is a lot of typing. A template system would let the operator
declare named templates in `configs/grant_templates.yaml`
(`financial-read`, `family-send`, `dev-session-bootstrap`) and
invoke them as `/grant --template <name>`. The template system
composes with the new pattern validator (#170/#171 above).

**Effort:** ~80 lines + a small spec for the template language.

## Recommended next bundle

If the next commit cycle ships ~600 lines, the highest-ROI
bundle is:

1. **#1 Daemon-shutdown teardown** — closes the most-painful
   loose end. ~30 lines, low risk.
2. **#2 Cross-file audit verification** — completes the audit-
   integrity story for any operator who actually uses
   rotation. ~80 lines.
3. **#7 First-use prompt distinct UX** — small UX win that
   makes the security mechanism legible. ~40 lines.
4. **#6 Stale-bundle TTL** — closes T14 with the same shape
   as the approval TTL we shipped. ~60 lines.
5. **#4 Per-counterparty reputation tier** as the larger
   item — touches the most operator-visible surface. ~200
   lines.

Total: ~410 lines + tests. Fits one commit cycle. Addresses
the loudest operator complaints from cookbook review.

Items #5, #9, #10 are good follow-ups for a second cycle. The
spec-level items (#11, #12, #13) deserve their own spec
documents before code.

## Discipline check

Same litmus test from the v1 roadmap Part 3 applies:

> Does the proposal reinforce Principles I–VIII?

Every item above passes — none introduce new threat models,
none reinvent prior art, none require kernel-level integration.
The discipline holds.

Items NOT on this list (and why):

- **eBPF / Tetragon / Cilium kernel hardening** — same
  rejection as v1 Part 3. Off-mission for the policy-engine
  contribution.
- **NeMo Guardrails / Cisco AI Defense integration** — addresses
  a different threat model (content-channel adversarial
  prompts). capdep deliberately assumes the LLM may be
  adversarial; bolting on content classifiers doesn't
  reinforce the capdep claim.
- **Differential privacy budgets** — solves a different
  problem (statistical leakage). The cookbook's aggregation-
  control gap is real but the rate-limited-capability
  mechanism we shipped (P2.6) is a better fit.
- **GUI rewrite** — capdep is terminal-first. Rich Textual
  surface already exists for operators who want more than the
  line REPL.

The branch is open for the next pull from this queue. The arc
from `2a53e51` → `da44d21` (26 commits) closed v1 — v2 is the
queue when the next arc begins.
