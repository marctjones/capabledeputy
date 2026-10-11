# Daily-driver quality plan — 2026-10-10

Status: **proposed**. Consolidates five independent reviews (tracker, daemon/security, macOS client UX,
test suite, documentation) of CapableDeputy at `0afe114`. Sequencing here is a recommendation; the owner
decides. Canonical status remains `ROADMAP.md` and the GitHub tracker.

Tracker state after this review: milestones renamed per §4 (new milestone 58 "03 Email"), #311/#336/#337 merged
into #338/#335, labels normalised, and 51 new issues filed as #509–#559 (security #509–#514, workflows
#515–#529, native UX #530–#539, testing #540–#552, docs #553–#559). Remaining audit work not yet applied:
rewriting milestone descriptions, body restructures of the 23 oversized issues, and the #455/#457/#458/#479 splits.

Progress on PR #562 (branch `chore/daily-driver-plan-2026-10`): reproducing CI at `0afe114` found and fixed five defects that
had been invisible because the author's uncommitted tree contained the fixes — a client-parity manifest gap, a stale strong-auth test,
cross-file `private` Swift views (clean checkout did not compile), an unformatted test file, and nine pyright errors in committed tests.
Also done: local-only planner guard made an allowlist (hosted model specs bypassed it), Podman tests skip when the engine is down,
menu-bar status no longer claims "protected", approval notifications name the newest unseen approval and no longer offer a blind Deny,
real-socket tests for client reconnect and mid-run revocation, and `scripts/verify-clean-checkout.sh` to check *committed* state.
Lesson recorded on #503: the "reproducible build" claim must be verified from a clean checkout, never the working tree.

## 1. Verdict

CapableDeputy's *structural* security core is real (single `decide()` chokepoint, always-on conflict floors,
raise-only labels, attenuating delegation, pinned approval re-dispatch) and the email-review moment in the
Mac app is genuinely well designed (exact bound envelope, consequence-named button, authorization ≠ execution ≠
delivery, edit withdraws consent). Three things keep it from being a daily driver:

1. **Workflows are thin.** ~45 of 85 open issues are evidence/contract infrastructure; none cover inbox actions
   (archive/flag/move), reply-all, attachments, calendar creation, reminders, backup/restore, undo or upgrade.
2. **The path to and around the strong moment is weak.** The command palette, task panel and global hotkey
   that the design thesis relies on are never instantiated; non-email approvals dead-end; notifications carry
   the oldest pending approval and offer a blind Deny; status copy overclaims ("CapDep protected").
3. **The evidence pipeline is unhealthy.** CI on `main` has been red since 2026-09-10 (coverage-ratchet step), local `main`
   is 61 commits ahead of `origin/main`, the local venv's interpreter lives in `/tmp` and gets purged, and
   only email has a dense real-socket e2e suite.

Separately, the daemon review found places where RPC/control-plane surfaces can mutate privilege outside the
`decide()` chokepoint. Details are held privately pending a disclosure decision (the repo is public); the public
umbrella item is **"Harden the daemon control plane against agent-reachable privilege mutation"**.

## 2. Grounding

| Domain | Sources used | Requirement drawn |
|---|---|---|
| Protection principles | Saltzer & Schroeder 1975 | complete mediation incl. RPC surface; fail-safe defaults; separation of privilege |
| Ocap / confused deputy | Hardy 1988; Miller 2006 | no ambient authority; no path for a subject to mint its own authority |
| IFC | Denning 1976; Bell–LaPadula; Biba; Myers–Liskov DLM | monotone taint; declassification is an owner-authorized audited act; control-plane integrity |
| Agent injection | Willison lethal trifecta; CaMeL (arXiv 2503.18813); Design Patterns (arXiv 2506.08837); OWASP LLM/Agentic Top 10 | structural trifecta denial; small-field quarantine schemas; tool output is data; no privilege-mutating tools on agent surface |
| Risk | Cox on risk matrices; FAIR; NIST AI RMF, SP 800-53 | avoid ordinal-only thresholds; tamper-evident audit |
| Usable security | Whitten & Tygar; Adams & Sasse; Yee 2002; Cranor C-HIP; Akhawe & Felt; Bravo-Lillo; Felt et al. | easy path = safe path; rare, meaningful prompts; no habituation; truthful status |
| HCI / HAI | Nielsen heuristics; Norman; Apple HIG; Microsoft HAI guidelines; Google PAIR; Horvitz; Lee & See | recoverable errors; calibrated trust; expectation-setting; plain language |
| Testing | AgentDojo; InjecAgent; Hypothesis stateful; mutation testing; XCUITest accessibility audits; Pact-style contracts | state-checked adversarial corpus; stateful label machine; shared RPC fixtures |
| Docs | Diátaxis; MADR; Keep a Changelog; arc42/C4 | tutorials / how-to / reference / explanation split; ADRs for owner decisions |

URLs are in the review reports (scratchpad of the review session); the full list should be copied into
`docs/explanation/references.md` when the docs restructure lands.

## 3. Workstreams

### W0 — Restore trust in the gate (do first)
- Push `main` and make CI green; resolve the coverage-ratchet failure with tests or a reviewed re-baseline.
- Move the uv-managed interpreter out of `/tmp`; make `test-capdep.sh` fail fast on a broken venv.
- Reconcile CLAUDE.md with reality: the ratchet is 88 per-surface floors (≈18%–100%) with 1.5-point tolerance, not "per-module 100%"; one flake policy.
- Add a `quarantine(issue, until)` marker and a non-blocking lane for the two known live-daemon flakes.

### W1 — Daemon control-plane hardening (private details; public umbrella only)
Operator-gate every RPC that mutates privilege; derive principals from the authenticated surface, never parameters;
exempt structural floors from shadow enforcement; human-presence proof for operator-only mutators; keyed audit chain;
honor `destructive_hint` everywhere; delimit tool results; add invariant tests for every item.

### W2 — Everyday workflows (the daily-driver gap)
Proposed new milestone **03 Email — everyday triage actions, reply-all and attachments**:
triage actions (archive/read/flag/move), reply-all/CC, attachments (quarantined read, hash-bound send), trusted-recipient
setup in-app; then calendar write (exact review, reconcile-by-reread), reminders over the task store, undo/revert for
reversible effects, daily activity digest, multi-account, backup/restore, upgrade path, clean-account onboarding acceptance.

### W3 — Native client UX
Blockers: wire or delete palette/task panel/global hotkey; non-email approvals show exact facts + an operator route
instead of a dead end; notifications carry the newest id and no blind Deny; grant scope choice + revoke in-app;
status copy states connection facts not safety. Majors: scoped persistent errors (not a 5-second global),
GUI-actionable daemon-down/onboarding recovery, translate taint/label/enforcement jargon, decorative context chips
(attach or remove), purpose switch must not silently fork the session, shorten summary→reply→review to ≤2 clicks,
accessibility baseline (text styles, labels, focus, audit), remove dead `ApprovalDetailView`, parity-contract drift
(`approval.task_list`, `email.draft_begin_edit`).

### W4 — Test architecture
Eight layers (static → unit → property/stateful → adversarial corpus → socket e2e → shared RPC contract → client UI →
live). Priorities: socket e2e for client reconnect-after-kill, mid-run revocation, image generation, first-run, and one
test per workflow in `docs/daily-driver-validation.md`; generated RPC contract fixtures shared by Python and Swift;
XCUITest target with `performAccessibilityAudit`; stateful Hypothesis machine over label state and approval lifecycle;
InjecAgent-style corpus through the hostile-planner harness; scheduled mutation testing on `policy/`, labels,
`approval_identity`; branch-coverage floors on policy surfaces.

### W5 — Documentation (Diátaxis)
Collapse four status narratives to one canonical source; fix mkdocs (dead config, 14 broken symlink links) or drop it;
add Install/Quick-start, glossary (capability, label, taint), SECURITY.md, user-facing threat model, ADRs for the five
resolved spikes and two owner decisions; fix stale DESIGN.md (OPA/Starlark, test counts) and RUNBOOK (nonexistent commands);
CHANGELOG hygiene (122 commits under one heading, link refs stop at 0.53.0, Releases stop at v0.53.0); archive evidence/handoff/vendor files.

## 4. Milestone structure (proposed)

`01 Email — usable native review on a reproducible build` · `02 Email — reliable daily use` ·
`03 Email — everyday triage actions, reply-all and attachments` (new) · `04 Documents — protected local output, then approved routing` ·
`05 Read-first — retrieval, briefings and calendar` · `Evidence — bounded authority and information flow` ·
`Theme — CapDepMac production UX` · `Theme — Model runtime quality and provisioning` ·
`Later — Vision` · `Later — Flow-aware planning` · `v1.0 — Hardening and daily-driver acceptance` · `Unscheduled — Distribution (deferred)`.
Close milestones 45 (superseded spikes) and 50 (one issue). Drop dead "NN Product —" ladder prefixes; assign versions at release.

## 5. Recommended order

W0 → 01 → 02 → **03 (triage actions)** → calendar write ∥ documents → read-first → v1.0 acceptance.
W1 runs immediately and in parallel (small, high-leverage changes). W3 blockers ride with 01/02; W4 L4/L5 items ride with each workflow.
This moves "03 triage" ahead of the documents/IFC demo so the product is usable daily before it showcases its thesis on invoices — an owner call.

## 6. Daily-driver workflow catalogue (acceptance targets)

| # | User phrasing | Target | Checkpoints | Automated test |
|---|---|---|---|---|
| D1 | "Summarise my unread mail" | grant card first time → summary with coverage/omissions | scoped read grant; posture "includes email content" | fixture inbox, socket + native |
| D2 | "Draft replies to the important ones" | ≤3 actions summary→review→send | exact envelope; edit withdraws consent | extend `test-email-mvp-gui.py` |
| D3 | "Find the contract PDF; renewal date?" | file grant (path verbatim) → answer | sensitive tier after read; egress blocked with safer-path offer | fixture FS |
| D4 | "Move my 3pm to 4" | effect-specific review → confirm | destructive gate; attendees named | fail-closed now; per-effect after #502 |
| D5 | "Remind me at 9" | proposal → confirm, or honest "can't yet" | reviewed effect | unsupported-copy assertion |
| D6 | "Generate an image of…" | progress, cancel, inline result | verified paths only | socket e2e with fake backend |
| D7 | "What did you do today?" | plain Activity digest | authority source per row | Activity fixture |
| D8 | "Undo that" | revert or honest irreversibility | never claim undo of a send | no-undo-for-sent assertion |
| D9 | First run | connect → model → account → first safe workflow | no ambient authority | no-daemon + synthetic daemon |
| D10 | Daemon dies mid-turn | status + recovery actions, no replay | Send disabled in open review | kill daemon during stream/review |
| D11 | Approval arrives while reading | count++, no focus steal, notification names newest | no blind decision from banner | two-pending routing test |
| D12 | "Stop letting it read my mail" | Active grants → Revoke | audited | revoke test |
| D13 | Switch purpose | explicit new-conversation confirm | no silent fork | model test |
| D14 | Keyboard/VoiceOver only | D2, D3, D11 completable | focus return, single announcement | human-recorded |

Seven-day protocol (entry gate: green CI at revision R, clean daemon revision; daily pre-gate; pass criteria:
≥5/7 days with a completed workflow, zero unauthorized effects, ≤25% unnecessary approvals, median latency ≤120 s summary,
≤30 min total recovery, no manual relaunch after restart, recipient-observed receipt for controlled sends, mean self-rating ≥3.5/5).

## 7. Open owner decisions

1. Security disclosure path for the private findings (advisory + private branch vs. public issues).
2. Reorder: put "03 triage" before documents (recommended) or keep current order.
3. Approve milestone renames/closures and the label-taxonomy cleanup (`epic`/`bug`/`enhancement` → `kind/*`).
4. Push `main` (61 commits) so CI can run.
5. Whether v1.0 "upgrade" means `git pull` + rebuild while distribution (#43) stays deferred.
