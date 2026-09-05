# Stabilization execution evidence — 2026-09-05

Status: implementation and automated assurance improved; stable MVP acceptance
is still open. No release was published and no tracker issue was closed.

## Implemented

- Daemon stop for an explicit socket never reads, waits on, signals, or removes
  the default daemon PID file. The previous success path could stop the wrong
  instance; this was reproduced during lifecycle validation and repaired.
- Override CLI commands have no client-local fallback authority store. Outages
  fail clearly for request, attest, list, show, and refuse.
- Native-tool denial coverage spans all shipped registrations; static CI
  tripwires catch direct handler calls and forbidden client authority imports.
  These checks are not a formal proof of all dynamic execution paths.
- MLX serializes generation and retains one weights pair. Cancellation stops
  at the next token boundary; model loading and an active kernel cannot be
  interrupted. This trades parallel throughput for bounded memory.
- Audit tail reconstructs only requested events; provenance reconstructs only
  provenance events. Neither query changes audit records or their hash chain.
- CapDepMac defaults to a host-owned daemon; GUI ownership requires explicit opt-in.
- TUI handles RPC completion after its conversation screen has been removed.
- Parity smoke has bounded RPCs, requires completed status plus exact output,
  and cancels its own failed/timed-out probe.
- Test state is isolated from real memory databases and real upstream configs.
- Active version guidance is aligned to 0.58.0; the original labeling plan is
  marked historical. Model measurements are distinct from measurement plans.

## Automated checks

- Full Python suite: **3,407 passed, 21 skipped**, no deselections; 271.46 seconds.
  The temporary execution log is no longer present after session recovery.
- Final focused tests after the TUI teardown fix: **26 passed** (TUI, model-quality
  plan, strict parity smoke). The temporary execution log is no longer present.
- Coverage ratchet: **passed all 85 enforced groups**; 32 remain below the
  aspirational 85% target. Baselines were not lowered.
- Whole-tree Ruff lint and format checks passed. Pyright: **0 errors**.
- Swift: **58 tests, one skipped, zero failures**. The actual Xcode app bundle
  also built successfully. Build-only evidence is not GUI acceptance.
- Earlier sandbox/disk-full runs were discarded as release evidence. A coverage
  append run overlapped a fresh coverage run and was discarded; the final full
  suite regenerated the coverage report used for the ratchet.

## Live runtime evidence

The existing active audit log was approximately 17 MB. After the query fix:

| RPC | Observed duration | Result |
|---|---:|---|
| ping | 0.036 s | healthy |
| audit.tail (5 events) | 0.049 s | five events |
| provenance.graph | 1.599 s | 3,449 nodes, 2,768 edges |

Before the fix, audit/provenance queries exceeded the smoke's 10-second timeout.
These timings are single observations under concurrent desktop load, not p95 SLOs.

The Mac app was inspected through its real UI and accepted a keyboard-entered
chat prompt. That attempt did not establish successful completion; after a
restart it reported failure. The native Computer Use bridge then became
unavailable. GUI-only override, approval, onboarding, reconnect, and document
workflow acceptance remain open. The final bundle must be relaunched for GUI
acceptance of the new supervisor default.

Connector readiness: Gmail reported connected; Calendar and Drive reported
missing credentials. Readiness does not prove successful live workflow execution.
No external messages were sent.

## MLX measurement and local configuration

[Raw synthetic results](evidence/model-qwen3-8b-2026-09-05.jsonl): cached
`/Users/marc/Models/Qwen3-8B-4bit-mlx`, MLX-LM 0.31.3, thinking disabled,
256-token cap, three repetitions of three cases. All **9/9** passed.
Cold response: **13.75 s**. Warm responses: **1.18–3.29 s**. Peak MLX memory:
**4.84 GB**. Cases cover exact output, tool selection, and an untrusted quoted
instruction. No tools execute. This is not a security guarantee or a measured
winner against 14B; real workflow comparisons are still required.

The machine's existing LaunchAgent now assigns all text roles to that cached
8B model as a reversible operational fallback. Quarantined extraction keeps its
no-tools boundary even when it uses the same weights. Repository model defaults
are unchanged. The prior temporary LaunchAgent backup is no longer present after
session recovery. The current launcher setup
script regenerates its plist; preserve/reapply these environment overrides if
rerunning setup. Do not claim permanent provisioning work (#339) is complete.

## Remaining release gates

1. Actual GUI completion of research memo, local document revision, and inbox
   draft workflows, including unauthorized variants and cancellation/recovery.
2. Click-through acceptance for #319 and #331–334 after relaunching the final app.
3. Credential/account acceptance for #325/#328; Calendar and Drive are not ready.
4. Seven days of real use in [dogfood-log.md](dogfood-log.md).
5. Comparative model/workflow measurements before any repository default promotion.
6. Improve startup readiness: configured upstream servers initialize serially
   before the daemon opens its socket; this machine takes minutes with 12 servers.

## Lightweight test model follow-up

The user requested lower resource use after a session interruption. Model loading
was not established as the crash cause. Disk exhaustion and the wrong-daemon
shutdown bug were observed; the latter is fixed.

Downloaded Qwen2.5-0.5B-Instruct-4bit (revision
`a5339a4131f135d0fdc6a5c8b5bbed2753bbe0f3`).
[Raw results](evidence/model-qwen25-05b-2026-09-05.jsonl): 3.17 s cold,
0.18–0.41 s warm, 0.40 GB peak MLX allocation (not whole-process RSS).
Six tool probes passed; all three exact-output probes failed capitalization.
No tools executed. This is infrastructure evidence, not quality acceptance.

`sh scripts/with-smoke-model.sh COMMAND ...` provides an opt-in offline tiny-model
profile for all roles, with 128-token role defaults and thinking disabled.
Explicit per-call token limits still apply. The wrapper overrides inherited model
role variables and must wrap the daemon process, not an attached client. It does
not alter the existing LaunchAgent or isolate application state. Unit/policy tests
continue using scripted fake clients.

Follow-up validation: 22 focused model routing, profile, version, and quality-plan
tests passed; launcher role inspection confirmed all five clients use the tiny
model with 128-token defaults. Shell syntax and changed Python lint/format passed.

## Guarded GUI follow-up

The resource watchdog triggered on sustained system memory warning (15 seconds)
while the daemon group was approximately 36 MiB resident. CapDepMac showed
Disconnected. This is a real safety-stop observation, not a unit-test simulation;
it does not establish that CapDep caused the system pressure. Other research
processes were active. The daemon was restarted only after pressure returned to
normal, with its tiny-model watchdog configuration intact. The GUI automatically
returned to Connected 0.58.0 without relaunching the app.

The actual GUI accepted a synthetic note and completed an accurate one-sentence
summary. This verifies conversational UI operation, not the full local-document
workflow with reviewed file effects. A counting probe completed at its response
limit before cancellation was confirmed. A longer story probe showed repetitive
small-model output; the attempted UI cancellation did not establish an interrupted
terminal state. GUI cancellation acceptance remains open. During these probes,
sampled process-group RSS stayed below approximately 620 MiB and memory pressure
was normal. No external messages were sent.

Routine `run-local-daemon-launchd.sh start/restart` now preserves an existing
LaunchAgent, preventing loss of operator model or watchdog settings. Regression
tests run both commands against isolated fake launch tools and check that the
existing plist is byte-for-byte unchanged. Launcher, guard, and setup-domain
checks: **23 passed**. Lint and shell syntax passed. No full-suite rerun was needed
for this shell-only behavior change.

## Queued cancellation race

A deterministic isolated-daemon test held startup before cancellation-scope
registration, issued Stop, then released startup. Before the fix the turn invoked
the model and completed despite carrying `cancel_reason=stop-before-start`.
Registration now replays pending cancellation under the same lock used by Stop,
and checks cancellation before entering model/tool execution. The regression
verifies zero model calls, an interrupted result, and successful completion of
the next turn in the same session.

Broader focused validation: **31 passed** (turn lifecycle, streaming, client-turn
stress, workstream ownership, watchdog). An earlier combined run hit an OS
PermissionError when signalling a watchdog test child; the isolated host-permitted
rerun passed, but the cause of that intermittent permission error is not proven.
Lint and patch whitespace checks passed.

Further real-GUI Stop attempts raced with short model responses and ended in
Completed; these do not count as cancellation acceptance. A fresh-session story
prompt also received a model refusal. Full GUI cancellation remains open.
System memory pressure returned to warning at the end of this check, so the
running daemon was not restarted to load the queued-cancellation patch.

## Model-free GUI cancellation and immediate completion

Added a manual harness: `.venv/bin/python -m tests.manual_gui_fake` from the repo
root. It refuses a live socket, uses a temporary state/audit directory, registers
real handlers without upstream servers, and assigns a scripted fake model.
`[slow]` yields a partial response and waits 120 seconds; other prompts finish
immediately. The shared integration fixture now supplies method discovery,
image readiness handlers, skill handlers, and policy-backed override handlers
needed for an honest GUI handshake. This is a test harness, not a production mode.
Do not use connector/setup/file-effect controls during this chat-only harness;
not every handler's ancillary configuration path is redirected by the fixture.

Actual GUI Stop closed the fake stream and the daemon reported `interrupted`
with reason `operator_stop`. The old GUI incorrectly displayed Completed. The
Swift source now preserves an Interrupted prompt status for streamed and polled
results rather than overwriting it with Completed. **Build/visual verification
of that label fix is pending:** a two-job Swift build was terminated when system
memory pressure remained at warning; no passing Swift result is claimed.

An immediate fake reply exposed a separate delivery race: completion could occur
before the GUI subscribed, leaving it on Starting turn indefinitely. The daemon
now joins a turn subscription before reading terminal state and sends the
terminal snapshot to that subscriber if already finished. A racing live terminal
event and snapshot may both arrive; clients should treat terminal state as
idempotent. This does not replay historical intermediate events or mutate turns.
A deterministic late-subscriber regression passes.

With the backend fix active, the actual GUI completed an immediate reply, stopped
a slow fake response, then completed a follow-up in the same session. RPC status
confirmed completed / interrupted(operator_stop) / completed. **29 tests passed**
across IPC, lifecycle, client-turn stress, and ownership. Ruff and whitespace checks
passed. No real model was loaded, and no external connector actions were used.
The fake daemon was shut down after testing; the normal model daemon remains off.
