# Onguard Clients

Onguard clients are headless CapDep clients that run background work from
deterministic schedules, queues, or approved configuration. They are not a new
authority tier. They are normal daemon clients with no direct access to tools,
credentials, policy internals, or trusted state except through daemon RPCs.

## Architecture Decision

CapDep should remain one security kernel with many clients. Background
automation should be extensible through onguard clients instead of putting
every product workflow inside the daemon.

- **Daemon owns authority.** Policy decisions, labels, capabilities, approvals,
  tool dispatch, connector credentials, provenance, audit, durable settings,
  and shared client coordination state stay daemon-side.
- **Onguard clients own orchestration.** They can schedule work, poll queues,
  assemble workflow inputs, run retry/backoff loops, and request daemon
  sessions/tools, but every effect still crosses the daemon chokepoint.
- **Interactive clients own human interaction.** GUI/TUI/CLI/MCP-control
  create schedules, approve AI-suggested jobs, inspect queues, review previews,
  and read results. They do not execute onguard work directly.
- **AI suggestions are drafts.** An LLM may propose a recurring job or changed
  interest profile, but the daemon stores it as a draft until a human or policy
  rule explicitly approves it.

## Why Not Put This All In The Daemon?

The daemon should not become a giant application server. Its job is to enforce
CapDep's safety model and host shared state. Onguard clients let us add daily
briefings, newspaper digests, file watchers, inbox triage, reminder processors,
and sync jobs without expanding the daemon's trusted codebase for every
workflow.

The daemon owns the infrastructure these clients use to coordinate safely:

- A **client registry** describing known clients, client kind, owner, version,
  allowed schedule names, and policy identity.
- A **client configuration store** for daemon-owned, schema-validated config.
- A **client command queue** for interactive clients to enqueue work for
  headless clients.
- A **client event/result stream** for onguard clients to report progress,
  previews, failures, and completed artifacts back to interactive clients.
- A **labeled artifact store** for previews/results that keeps source
  provenance, session/client/schedule/command references, and promotion state.
- A **scheduler contract** for deterministic recurrence, leases, run history,
  run-now, and disabled or paused schedules.

These are coordination protocols. They are not replacement tool paths.

## Policy Model

Every onguard run should create or reuse a daemon session with explicit origin
metadata. Policy and Starlark should be able to distinguish at least:

- `origin.kind`: `human_interactive`, `mcp_control`, `onguard`, `scheduled`,
  `queue_worker`, `system_internal`.
- `origin.client_id`: stable id such as `onguard.digest.daily`.
- `origin.schedule_id`: optional id for a recurring schedule.
- `origin.command_id`: optional id for one queued command.
- `origin.proposed_by`: `human`, `ai`, `policy`, or `system`.
- `origin.approved_by`: human or policy identity when applicable.

This should feed Axis D and the decision-refinement layer. Starlark inspectors
should receive this structured origin, so rules can say:

- Daily digest may read approved news and calendar sources, but cannot send
  email.
- Inbox triage may classify messages and write previews, but cannot archive or
  delete without approval.
- AI-proposed schedules require human approval before first run.
- A specific onguard client may run only from its declared schedules.
- Scheduled runs may have lower notification friction but stricter write or
  external-egress rules than foreground human sessions.

## Shared State And Communication

Do not overload `memory.*` as a generic message bus. Memory is user/domain data
with labels that propagate into sessions. Client coordination needs separate
daemon-owned stores with explicit semantics.

Recommended daemon RPC families:

- `client.registry.list`
- `client.config.get`
- `client.config.propose`
- `client.config.approve`
- `client.config.update`
- `client.queue.enqueue`
- `client.queue.claim`
- `client.queue.complete`
- `client.queue.fail`
- `client.events.list`
- `client.events.publish`
- `client.events.ack`
- `artifact.create`
- `artifact.read`
- `artifact.list`
- `artifact.promote`
- `artifact.delete`
- `schedule.list`
- `schedule.create`
- `schedule.update`
- `schedule.disable`
- `schedule.run_now`
- `schedule.claim_due`
- `schedule.complete_run`
- `schedule.fail_run`
- `schedule.history`

Each record should carry labels, provenance, actor identity, timestamps, and an
audit id. Queue items that contain untrusted data must remain labeled. Results
that summarize sensitive inputs must inherit labels or pass through an explicit
declassifier.

## Daily Newspaper / Digest Pattern

The daily newspaper should be an onguard client, not daemon core logic.

`onguard.digest.daily` should:

1. Claim an approved schedule from the daemon.
2. Open a daemon session with origin `onguard` and schedule metadata.
3. Read allowed sources through daemon-mediated tools.
4. Record provenance for every source item considered.
5. Rank items against a daemon-owned interest profile.
6. Produce a preview artifact with inherited labels.
7. Request approval before external publication or high-friction notification.
8. Publish a client event so CapDepMac, CLI, TUI, or MCP-control can show the
   result.

The interest profile should be explicit and inspectable. It can include
operator-pinned topics, inferred topics, source preferences, exclusions,
recency windows, and examples of useful/not useful items. Inferred updates
should be drafts or low-risk suggestions until accepted.

## Practical Implementation Order

1. Add origin metadata to session creation and policy/inspector inputs.
2. Add daemon-owned client registry and config store.
3. Add daemon-owned queue and event/result stream.
4. Add scheduler records and lease/history semantics.
5. Implement one onguard client: `onguard.digest.daily`.
6. Expose all of the above through CLI, Swift GUI, TUI status, and MCP-control
   parity.

This keeps onguard clients extensible while preserving the single daemon
chokepoint.
