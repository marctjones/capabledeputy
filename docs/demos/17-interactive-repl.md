# Demo 17: Interactive REPL — Talk to the Agent

**Audience:** anyone who wants to try CapableDeputy hands-on without
writing any code.
**Time:** ~5 minutes per scenario.
**Requires:** an Anthropic API key in the environment.

The interactive REPL drops you into a chat session with the real
agent loop and stubbed tools pre-seeded with realistic data. Three
scenarios ship in this release: `daily-briefing`,
`untrusted-research`, and `accountant`. The point of the REPL is to
make the security properties visible — type a prompt, watch the
policy engine ALLOW or DENY each tool call, see the gated
declassification path when approval is required.

## What the demo proves

1. The full agent loop works against a real LLM with stubbed tools.
2. Tool dispatches are labelled, audited, and policy-checked
   identically to programmatic mode — you can see every decision in
   the per-turn trace.
3. The approval workflow is end-to-end: when an outcome surfaces as
   `require_approval`, the user can submit an approval, review the
   verbatim payload, and approve from the REPL; the daemon spawns a
   purpose-limited session and dispatches the action.

## Setup

```bash
# Terminal 1: start the daemon (pick your model)
export ANTHROPIC_API_KEY=$(cat CLAUDEAPI.KEY | tr -d '[:space:]')
export CAPDEP_LLM_MODEL=claude-haiku-4-5
uv run capdep daemon start
```

```bash
# Terminal 2: pick a scenario
uv run capdep demo list
uv run capdep demo start daily-briefing
```

The `demo start` command:
- Creates a fresh session with the scenario's `intent` and capabilities
- Seeds the inbox / calendar / memory stores with hand-curated data
- Prints the intro, suggested prompts, and a security note
- Drops you into the chat REPL

## Slash commands

Slash commands are user-only — they pass straight through to the
JSON-RPC daemon and are never visible to the LLM (which sees only the
free-text turns).

**Session control**
```
/sessions               list all sessions
/session [id]           details on current or another session
/switch <id>            retarget REPL to another session
/whoami                 print current session id
/spawn <intent>         create a clean child session (TRUSTED_USER_DIRECT, no inherited labels) and switch to it
/abort [id]             abort a session (current if no id)
/grant <KIND> <pattern> [--one-shot --destructive --max-amount N]
/status /labels /caps   inspect current session
```

**Approvals**
```
/approvals              list pending approvals
/approve <id>           review verbatim payload, prompt y/N, approve
                        — runs the action in a purpose-limited
                          session for SEND_EMAIL, QUEUE_PURCHASE,
                          and destructive ops (MODIFY/DELETE)
/deny <id>              deny a pending approval
/submit                 interactively submit an approval
/remember <ACTION> <target-pattern>
                        install a pattern rule so future matching
                        approvals auto-approve
```

**Declassification**
```
/schemas                list quarantined-extract schemas
/extract <msg> <schema> run quarantined LLM over an inbox message;
                        result is label-stripped and safe to paste
                        into a /spawn-ed clean session
```

**Trace / observability**
```
/trace                  re-render the last turn's outcomes
/audit [N] [--full]     last N audit events for current session
                        — --full dumps each event payload as JSON
```

**Misc**
```
/help                   list slash commands
/quit                   exit
```

## Auto-submit and the approval flow

When a tool returns `require_approval`, the REPL **auto-submits** the
approval request — you don't manually `/submit`. You'll see:

```
  require_approval purchase.queue rule=financial-meets-purchase
→ approval #3 enqueued. review with /approve <id> or list with /approvals.
```

`/approve 3` then renders the verbatim payload, prompts y/N, and
dispatches in a purpose-limited child session. Currently auto-execute
on approve is wired for: `email.send` (SEND_EMAIL),
`purchase.queue` (QUEUE_PURCHASE), and the four destructive ops
(`memory.update`, `memory.delete`, `calendar.update_event`,
`calendar.delete_event`).

## History and completion

The REPL persists history at `~/.cache/capabledeputy/repl_history`,
and TAB-completes:

- All slash commands (`/sp<Tab>` → `/spawn`)
- Session UUIDs after `/switch`, `/session`, `/audit`, `/abort`
  (shown with `intent` as completion metadata)
- Approval IDs after `/approve`, `/deny`
- `CapabilityKind` values after `/grant`
- `/grant` flags (`--one-shot`, `--destructive`, `--max-amount`)
- Inbox message IDs after `/extract` (shown with subject + sender)
- Schema names after `/extract <msg> <Tab>`
- ApprovalAction values after `/remember`

A background thread refreshes the cache every ~1s so TAB never blocks
on the daemon.

## Scenario sketches

### `daily-briefing`
Three unread emails (one work, one newsletter, one personal) plus two
calendar events today. Ask "what's on my plate today?" — the agent
reads the inbox + calendar and summarises. Try asking it to forward
an email externally; `untrusted-meets-egress` will block.

### `untrusted-research`
Web fetch + email send capabilities granted. Ask the agent to fetch a
URL and summarise; then ask it to email the summary. The send is
denied — once the session has touched `untrusted.external` content,
no egress without an approval.

### `accountant`
Three memory entries labelled `confidential.financial`. Ask the agent
to summarise May spending; then ask it to email the accountant. The
send surfaces as `require_approval` (the `financial-meets-purchase`
rule fires on financial-meets-email as DENY in v0.3; financial-meets-
purchase is the approval path — try `/submit` for the purchase
variant if you want to see the gated declassification path live).

## Architecture note

The REPL is a thin client over the existing JSON-RPC daemon — no new
protocol surface. The seed step is a single new RPC (`demo.start`)
that creates a session and writes into the in-memory `Inbox`,
`CalendarStore`, and `LabeledMemoryStore`. Tool dispatches go through
the same `LabeledToolClient` that programmatic mode uses, so every
audit event you see in the REPL appears identically in
`capdep watch`.

## Files

- `src/capabledeputy/demo/scenarios.py` — `Scenario` dataclass and
  the three built-ins
- `src/capabledeputy/demo/seed.py` — `apply_scenario` writes to the
  App's stores
- `src/capabledeputy/daemon/demo_handlers.py` — `demo.list_scenarios`
  and `demo.start`
- `src/capabledeputy/daemon/extract_handlers.py` —
  `extract.inbox_message`, `extract.schemas`, `extract.inbox_ids`
- `src/capabledeputy/daemon/approval_handlers.py` — auto-execute
  paths for SEND_EMAIL, QUEUE_PURCHASE, EXECUTE_DESTRUCTIVE
- `src/capabledeputy/cli/chat.py` — REPL loop + slash command dispatch
- `src/capabledeputy/cli/completer.py` — TAB completer + cache
- `src/capabledeputy/tools/native/policy_preview.py` —
  agent-callable dry-run
- `tests/test_demo_scenarios.py`, `tests/test_repl_completer.py`,
  `tests/test_approval_destructive_executor.py`,
  `tests/test_extract_handlers.py`,
  `tests/test_tools_native_policy_preview.py`
