# CapableDeputy — Design

## 1. Vision

CapableDeputy is a **structurally secure runtime for personal AI agents.** It answers the question "ten months after CaMeL, where are the production-grade prompt-injection-resistant agents?" by building one — multi-provider, MIT-licensed, terminal-operated, designed for individuals who want capable AI assistance without surrendering health records, financial data, or third-party communications to the LLM's word-completion machinery.

It is not another perimeter classifier (Lakera, Cisco AI Defense, LlamaFirewall). It is not another sandbox runtime (NVIDIA OpenShell, NemoClaw). It is the **architectural layer** that propagates capabilities and information-flow labels through every action an agent takes, escalates to programmatic execution when stakes warrant, and forces every cross-compartment data flow through deterministic, human-auditable approval gates.

## 2. Threat Model

The class of attack we structurally prevent: the **lethal trifecta** — an agent with simultaneous access to (a) sensitive data, (b) untrusted content, and (c) outbound communication. Prompt injection embedded in untrusted content can convince any LLM to misuse its own capabilities to exfiltrate or act on data the user never authorized.

CapableDeputy guarantees that no LLM session can hold all three legs of the trifecta simultaneously without an explicit, human-approved declassification gate. Even if every classifier fails and the LLM is fully compromised by an injection, the policy violation cannot occur because the harness — not the LLM — controls capability dispatch.

We do not defend against:
- LLM logical errors *within* a granted scope (sending the right data to the wrong approved recipient).
- Compromise of the harness itself.
- Side-channel timing attacks across compartments.

## 3. Theoretical Foundations

CapableDeputy synthesizes five classical models:

- **Bell-LaPadula** for confidentiality classification — the lattice of label sets is partially ordered.
- **Biba** for integrity — untrusted-source data must be declassified through approved transformations before it can flow into trusted contexts.
- **Brewer-Nash (Chinese Wall)** for dynamic conflict-of-interest — once a session has read certain label classes, conflicting classes become unavailable in that session.
- **Clark-Wilson** for well-formed transactions — declassification, cross-session merges, and externally-visible actions are gated transactions, not free operations.
- **Object-Capability Model (Mark Miller's E)** — capabilities are unforgeable tokens held by the runtime, never by the LLM. The LLM *requests*; the runtime *grants*.

Layered above these is **information flow control** with provenance tracking: every value carries the union of labels of every value that contributed to it.

## 4. Architecture Overview

```
┌────────────────────────────────────────────────────────────────┐
│  Terminal Clients (Typer CLI · Textual TUI · capdep watch)     │
└────────────────────────────────┬───────────────────────────────┘
                                 │ JSON-RPC over Unix socket
┌────────────────────────────────▼───────────────────────────────┐
│  capdep daemon                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ Session      │  │ Policy       │  │ Approval Queue       │  │
│  │ Graph        │◄─┤ Engine (OPA) │  │ + Pattern Rules      │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────────────────┘  │
│         │                 │                                    │
│  ┌──────▼─────────────────▼───────────────────────────────┐    │
│  │  Mode Dispatcher                                       │    │
│  │  ┌─────────────────┐ ┌──────────────┐ ┌─────────────┐  │    │
│  │  │ Turn-Inheritance│ │ Dual-LLM     │ │ Programmatic│  │    │
│  │  │ (default)       │ │ (escalation) │ │ (Starlark)  │  │    │
│  │  └─────────────────┘ └──────────────┘ └─────────────┘  │    │
│  └────────────────────────┬───────────────────────────────┘    │
│                           │                                    │
│  ┌────────────────────────▼─────────────────────────────────┐  │
│  │  LLM Adapter Layer (LiteLLM)                             │  │
│  │  Anthropic · OpenAI · Gemini · local                     │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                │
│  ┌───────────────────────────┐ ┌────────────────────────────┐  │
│  │  Labeled MCP Client Pool  │ │  Audit Log (JSONL, append) │  │
│  │  - filesystem             │ │  + Trace events per turn   │  │
│  │  - email (stubbed in MVP) │ │                            │  │
│  │  - calendar (stubbed)     │ │                            │  │
│  │  - memory-space MCP       │ │                            │  │
│  │  - purchase-queue MCP     │ │                            │  │
│  └───────────────────────────┘ └────────────────────────────┘  │
└────────────────────────────────────────────────────────────────┘
```

## 5. Three Execution Modes

The mode dispatcher selects per-turn based on accumulated label set and proposed intent. Modes form a ladder of trust required vs. work allowed:

### 5.1 Turn-Level Inheritance (default)
- Any data entering context contributes its labels to the session's accumulated label set.
- All LLM output in a turn inherits the union of labels in context for that turn.
- Tool dispatch is gated against the label set at call site.
- **Cost:** 1× LLM call. **Limitation:** Once `confidential.health` enters context, every egress capability is blocked for the rest of the session.

### 5.2 Dual-LLM (escalation)
Triggered when policy detects that a useful action requires reading labeled data without contaminating the planner.
- **Quarantined LLM:** processes labeled data, emits only schema-validated structured output (typed values, opaque handles, bounded summaries). Holds no tool capabilities.
- **Privileged LLM (planner):** sees handles and metadata; never sees raw labeled data. Calls tools.
- Schemas are declassifiers — each field is a deliberate, audited declassification gate.
- **Cost:** 2× LLM calls per data-extraction step. **Defeats:** prompt injection by construction (injected text reaches only the quarantined LLM, whose output schema cannot encode tool calls).

### 5.3 Programmatic (high-stakes / multi-step)
- Privileged LLM emits a **Starlark program** describing the entire planned data flow.
- A **custom Starlark interpreter** with label-aware values executes the program. Every operation propagates labels through union; every tool call is gated at the call site against the live label union.
- The Starlark program is **statically inspectable before execution.** Auditable, replayable, dry-runnable.
- **Cost:** 1–2× LLM calls + interpreter time. **Uniquely enables:** pre-execution audit, deterministic label propagation across arbitrary computation, composable approved templates.

### 5.4 Mode Selection Policy
```
mode = select_mode(context_labels, proposed_intent, available_tools)
```
- Default → turn-level.
- Escalate to dual-LLM when context will need to *extract from* labeled data while planning over non-labeled tools.
- Escalate to programmatic when intent is multi-step over labeled data, or when stakes (egress to external recipients, financial actions, health declassification) exceed a configured threshold.
- Selection is logged, auditable, and the user can require manual mode escalation per-rule.

## 6. Session Graph

Sessions are first-class graph nodes:

```
Session {
    id: uuid
    parent: session_id | null
    children: [session_id]
    status: active | paused | waiting_approval | done | aborted
    label_set: set<Label>
    capability_set: set<Capability>
    history: [Turn]
    declassification_log: [DeclassEvent]
    created_at, updated_at, owner
}
```

Operations:
- **`fork(parent, intent)`** — child snapshots parent's labels/capabilities/history. Free, no policy gate (no information leaves the parent).
- **`pause(session)` / `resume(session, input)`** — stash and restore exact state.
- **`merge(a, b)`** — union label sets; any policy violation must be resolved before merge proceeds. **Gated.**
- **`grant(from_session, to_session, capability, scope)`** — capability handoff. **Gated when label sets differ.**
- **`message(from, to, payload, payload_labels)`** — labeled inter-session message. Receiver's policy decides accept/reject.

The runtime can context-switch between ready sessions; approval-blocked sessions don't freeze the daemon.

## 7. Labels and Capabilities (MVP scope)

### 7.1 Labels (8, orthogonal)
| Label | Meaning | Source |
|---|---|---|
| `confidential.health` | Medical/PHI data | filesystem paths matching health patterns |
| `confidential.financial` | Bank, brokerage, tax data | path patterns; specific MCP servers |
| `confidential.personal` | Calendar, contacts, notes | calendar/note MCP servers |
| `untrusted.external` | Web pages, third-party emails | web-fetch, inbound email MCP |
| `untrusted.user_input` | Paranoid-mode user input | opt-in mode |
| `trusted.user_direct` | User text via approval UI | user input through CLI/TUI |
| `egress.email` | Outbound email capability marker | email-send tool |
| `egress.purchase` | Outbound purchase capability marker | purchase-queue tool |

### 7.2 Conflict Rules (Brewer-Nash, ~5)
1. `untrusted.*` ⊕ `egress.*` → **deny without declassifier**
2. `confidential.health` ⊕ `egress.*` → **deny without declassifier**
3. `confidential.financial` ⊕ `egress.email` → **deny without declassifier**
4. `confidential.financial` ⊕ `egress.purchase` → **require approval (Clark-Wilson gate)**
5. `untrusted.external` content used as tool argument → **wrap argument in declassifier check**

### 7.3 Capabilities (6 types)
Each capability holds: target pattern, expiry (one-shot / session / persistent), origin (system-default / user-approved / pattern-rule), audit_id.

- `READ_FS(path_pattern)`
- `WRITE_FS(path_pattern)`
- `SEND_EMAIL(recipient_pattern)`
- `WEB_FETCH(url_pattern)`
- `CALENDAR(read | write)`
- `QUEUE_PURCHASE(vendor_pattern, max_amount)`

### 7.4 Tools

**Principle:** prefer upstream MCP servers wherever they exist. CapableDeputy provides labels and policy enforcement, not reimplementation. The MVP tool slate below is chosen for ecosystem reach (mapping to the most-used OpenClaw skills and the official MCP reference servers) and for coverage of the four canonical scenarios in §13.

| Tool | Implementation | Default labels | Scenarios |
|---|---|---|---|
| Filesystem (`fs.read`/`fs.write`) | upstream `modelcontextprotocol/servers/filesystem` | path-pattern → `confidential.health` for `~/health/*`, `confidential.financial` for `~/finance/*`, else `internal` | #1, #4 |
| Web fetch (`web.fetch`) | upstream `modelcontextprotocol/servers/fetch` | `untrusted.external` always | #3 |
| Email (`email.*`) | community Gmail MCP server | inbound: `untrusted.external`; outbound capability: `egress.email` | #1, #2 |
| Calendar (`calendar.*`) | community Google Calendar MCP server | `confidential.personal` | #1, daily briefings |
| Notes / Obsidian | community Obsidian MCP server | `confidential.personal` (default); per-vault override | #3, daily briefings |
| Labeled memory (`memory.*`) | **CapableDeputy-native MCP server** | inherits at write; unions at read | all |
| Purchase queue (`purchase.queue`) | **CapableDeputy-native stub** (always returns "queued for approval") | `egress.purchase` capability marker | #2 |
| GitHub | upstream `modelcontextprotocol/servers/github` | issue/PR comments by third parties → `untrusted.external`; own repos → `internal` | bonus dev demo |

Each upstream MCP server is wrapped with the Labeled MCP Client (§10.7); the server itself stays vanilla. We write only what the ecosystem doesn't already provide cleanly. This minimizes our trusted computing base and lets us inherit the upstream community's bug fixes and improvements.

## 8. Approval System

### 8.1 Approval Request
```
ApprovalRequest {
    id: int (monotonic)
    audit_id: uuid
    requested_at: timestamp
    from_session: session_id
    to_session: session_id | null  # null for cross-session, set for within-session capability grant
    action: SEND_EMAIL | DECLASSIFY | MERGE | GRANT | QUEUE_PURCHASE
    payload: bytes (verbatim, no LLM paraphrase)
    labels_in: set<Label>
    labels_out: set<Label>
    capability_requested: Capability
    justification: str  # model's stated reason, shown but never load-bearing
    status: pending | approved | denied | deferred | expired
    decision_at, decided_by, decision_scope
}
```

### 8.2 Approval UI Rules
- Payload shown **verbatim**, never paraphrased.
- Scope editable before approval (recipient pattern, expiry, retention).
- Audit ID assigned at request creation, logged regardless of outcome.
- Single-key approve action, **distinct from navigation keys** to prevent muscle-memory mis-approval.
- Approve/Deny/Defer/Modify-Scope.

### 8.3 Pattern Rules (Approval Fatigue Mitigation)
A pattern rule auto-approves matching future requests **but still logs them**. Constraints:
- Patterns must be specific. CLI refuses patterns containing `*` in critical positions (e.g., `SEND_EMAIL to=*` is rejected).
- Patterns expire after a configured TTL.
- Pattern usage is summarized in `capdep audit --pattern <id>`.
- Patterns are revocable instantly; revocation propagates to in-flight requests.

## 9. Trace and Observability

For a security framework, the interpretation pipeline between the LLM and the action it triggers IS part of the trust boundary. If the harness misinterprets an LLM response — extracting the wrong tool call, misclassifying a label, picking the wrong execution mode — security fails silently. CapableDeputy makes every step from raw LLM bytes to policy decision **inspectable, replayable, and auditable** as a first-class feature.

### 9.1 The Trace Primitive

A **trace** is the complete record of one LLM turn's processing: from the moment the daemon decides to consult the LLM to the moment a tool result lands back in context (or a policy denial halts the flow). Every trace is correlated by `(session_id, turn_id)`; sub-steps within a turn are correlated by `step_id`. Traces persist in the audit log indefinitely (subject to retention policy) and are queryable, replayable, and renderable in the TUI.

### 9.2 Event Taxonomy

The audit log records the following events for every turn. All events share a common envelope: `(session_id, turn_id, step_id, event_type, timestamp, payload)`.

- `llm.context_assembled` — context components broken out: system prompt, history slice, memory selections (with labels), tool results (with labels), label-driven redactions applied, final token count.
- `llm.request_sent` — full prompt as transmitted to the provider, including model identifier and parameters.
- `llm.response_received` — raw response text and metadata (latency, token usage).
- `llm.response_parsed` — parser's interpretation: extracted tool calls, extracted Starlark code (programmatic mode), schema validation result (dual-LLM mode), parse errors.
- `mode.selected` — execution mode decision, reason, label state at decision time.
- `policy.decided` — decision input (session state, action, args), output (allow/deny/require_approval), rule that fired.
- `label.propagated` — before/after label sets, source operation, propagation path.
- `capability.checked` — capability requested, capabilities held, decision, scope.
- `tool.dispatched` — server, tool name, sanitized args, audit_id.
- `tool.returned` — result body, applied result-labels, latency.
- `approval.*` — request created / approved / denied / deferred / expired (also covered in §8).
- `session.*` — created / forked / paused / resumed / merged / aborted (also covered in §6).

### 9.3 Trace Inspection — CLI

- `capdep trace <session>` — print the full trace for a session in chronological order.
- `capdep trace <session> <turn>` — print one turn's trace with all steps inline.
- `capdep trace <session> <turn> --component context|prompt|response|parse|policy|tool` — drill into a single component.
- `capdep replay <session> <turn>` — re-run parse and policy steps on the recorded LLM response. Read-only; does not mutate session state or re-execute tool calls.
- `capdep replay --range <session> <from-turn>:<to-turn>` — validate a policy change against a window of historical traces.
- `capdep queue` — unified view of all session queues: pending approvals, paused sessions, tool-blocked sessions, with the reason each is queued.

All commands support `--json` for scripting.

### 9.4 Trace Inspection — TUI

The TUI provides two additional views:

- **Trace pane** — drill-down for a selected turn. Steps shown in order with collapsible sub-views: context assembly → LLM call → parse → mode → policy → dispatch → tool result → label update. Each step shows its inputs, outputs, and any decisions made. Diff view for label propagation across steps.
- **Session graph view** — a switchable mode in the Sessions pane that draws the fork tree, so parent/child relationships and parallel branches are visible at a glance.

The Sessions pane also gains a status overlay showing why a session is currently queued (approval / tool latency / paused-by-user / paused-on-policy).

### 9.5 Replay and Policy Iteration

Because the audit log captures the literal LLM input and output for every turn, **policy rules can be iterated against historical traces.** Workflow:

1. Capture a trace where the agent did something undesirable, or was blocked when it shouldn't have been.
2. Modify the relevant policy rule.
3. Run `capdep replay <session> <turn>` to see what the new rule would have decided.
4. Run `capdep replay --range <session> <from-turn>:<to-turn>` to validate the change against a window of historical traces.

This makes policy authoring an evidence-based activity rather than a blind one. It is also how regression tests for policy rules are constructed: a known-good trace becomes a fixture against which future policy versions must continue to produce the same decision.

### 9.6 Why Trace Is Part of the Security Model

A common concern from security reviewers: *"how do I know the harness isn't just the LLM in another costume?"* The answer is structural: every step from raw LLM bytes to policy decision is captured by an entity (the audit log writer) that the LLM cannot influence. The trace is the verifiable claim that the runtime did what it said it did. Without trace inspection, the architectural guarantees described in §3 are unverifiable in practice — they become claims rather than evidence.

This is why trace is specified as a first-class component, not an afterthought logging layer.

## 10. Component Specifications

### 10.1 Daemon
- Python 3.12+, single process.
- `anyio` for structured concurrency.
- Owns: session graph, policy engine, agent loops, MCP connection pool, approval queue, audit log, configuration.
- IPC: JSON-RPC over Unix socket at `$XDG_RUNTIME_DIR/capdep.sock`.
- State persisted to **SQLite** at `$XDG_DATA_HOME/capdep/state.db`.
- Lifecycle: foreground process started in tmux/screen for development; systemd/launchd unit for deployment. No daemonization magic.

### 10.2 CLI (`capdep`)
- **Typer** with rich subcommand tree.
- Every command supports `--json` for scripting.
- Reads from daemon over Unix socket; never touches state directly.
- Sub-100ms p95 latency for all read commands.

Subcommand tree:
```
capdep                     # default: launch TUI
capdep daemon              start|stop|status|logs
capdep session             list|new|fork|merge|kill|tail|attach
capdep approval            list|show|approve|deny|defer
capdep send <session> ...  # send message into a session
capdep policy              show|edit|validate|test
capdep label               list|show
capdep memory              list|show|inspect
capdep tool                list|show
capdep run <prog.star>     # execute a Starlark program
capdep dry-run <prog.star> # static check + mock execution
capdep trace <session> [<turn>] [--component ...]   # inspect turn-level traces
capdep replay <session> <turn> [--range a:b]        # replay parse + policy on past traces
capdep queue                                         # unified queue view
capdep audit [filters]
capdep watch [filters]     # live event stream
capdep config              show|edit|validate
```

### 10.3 TUI (`capdep tui`)
- **Textual** framework.
- Full design is the five-pane layout below; v0.1 ships a minimum-
  viable three-pane subset (Sessions / Approvals / Events) with the
  approval modal. Conversation, Trace, and the session-graph toggle
  are v0.2 (ROADMAP.md).
- Modal: Approval request, with **verbatim payload** rendered byte-
  for-byte (DESIGN.md §8.2 hard rule). Single-key approve (`a`),
  deny (`d`), defer/cancel.
- Keybindings: vim-like navigation (`j/k`), explicit actions
  (`a/d/m/f`); `t` toggles trace pane focus; `g` toggles session-list
  / session-graph view.
- Multi-pane status updates via reactive bindings to daemon events
  (v0.1 polls every 1.5s; v0.2 adds streaming push).

Full target layout (v0.2):
```
┌─ Sessions ────────────┬─ Conversation: <selected session> ──────────┐
│ list OR fork tree     │ turns, labels per turn, input box           │
│ status overlays       │                                              │
├─ Approvals ───────────┼─ Trace: <selected turn> ─────────────────────┤
│ pending requests      │ context · prompt · response · parse · policy │
│                       │ · dispatch · result · label-diff             │
├─ Events ──────────────┴──────────────────────────────────────────────┤
│ live audit/event ticker                                              │
└──────────────────────────────────────────────────────────────────────┘
```

### 10.4 Policy Engine
- v0.1 ships a **pure-Python policy engine** with rules pinned in
  `policy/rules.py`; OPA/Rego integration is reserved as a future
  alternative if external rule authoring becomes important.
- Decision input: `(label_set, capability_set, action)`. Decision
  output: `Decision.{ALLOW, DENY, REQUIRE_APPROVAL}` plus the matched
  capability, fired rule name, effective labels, and human-readable
  reason.
- Decisions are pure functions over their inputs — exhaustively tested
  via parametrized test matrix.
- Every decision is recorded as a `policy.decided` trace event (§9.2).

### 10.5 Programmatic Mode (Starlark-style interpreter)
- v0.3 ships a Python-AST-subset interpreter (Starlark-shaped, not a
  full Starlark fork). LLMs already write Python natively; the AST
  subset is statically analyzable; labels propagate through every
  operation. The choice trades a syntax-purity goal for ~10× less
  interpreter code while preserving the security properties.
- **Allowed forms:** literals (int, str, bytes, bool, None, list,
  dict, tuple), name references, attribute access (read-only),
  arithmetic / comparison / boolean operators, `if/else` (statement
  and expression), `for` over a finite iterable, function calls,
  assignments, `return` from the top level.
- **Forbidden forms:** `import`, `class`, `def`, `lambda`, `try/except`,
  `with`, `while`, `global/nonlocal`, decorators, `del`, augmented
  assignments to attributes, set/dict comprehensions with side
  effects. Static parser rejects forbidden forms before any value
  is touched.
- **Value type wraps any Python value with `labels: frozenset[Label]`.**
- Every binary operator, function call, and attribute access
  propagates labels via set union over operands.
- Tool calls in the interpreter resolve to gated dispatches via
  the same `LabeledToolClient` used by turn-level mode; gate
  evaluated against the value's labels at the call site.
- Static analyzer (`programmatic.analyzer`) rejects programs that
  would unconditionally violate policy on a known label/capability
  state at any reachable point — caught before execution.
- Surfaced as `capdep run <prog.py>` and `capdep dry-run <prog.py>`.

### 10.6 LLM Adapter Layer
- **LiteLLM** for provider abstraction.
- Supported providers: Anthropic (`claude-opus-4-7`, `claude-sonnet-4-6`, `claude-haiku-4-5`), OpenAI, Gemini, local via Ollama.
- **Prompt caching enabled by default** for Anthropic; reduces context-assembly cost dramatically on repeated similar prompts.
- Per-call provider override (e.g., quarantined LLM uses Haiku for speed; planner uses Opus).
- Emits `llm.context_assembled`, `llm.request_sent`, `llm.response_received`, `llm.response_parsed` for every call.

### 10.7 Labeled Tool Client and MCP layer
The Tool layer has two complementary surfaces:

**`LabeledToolClient` (v0.1, internal)**: the single chokepoint for
every tool dispatch initiated by CapableDeputy's own agent loop.
Wraps the in-process tool registry. For each tool call: builds an
`Action` from the tool's args, calls `policy.decide()`, audits the
decision, dispatches the handler if allowed, propagates labels into
the session, emits the full §9.2 event sequence. Used by both the
agent loop (`run_turn`) and the MCP server's `tool.call` RPC.

**MCP server (`capdep mcp-server`, v0.1)**: stdio MCP server that
exposes the same tool registry to external MCP hosts (Claude Code,
etc.). Tool calls forwarded through `tool.call` so policy + audit
apply identically whether the agent loop is internal or external.
Spec compliance audited in `docs/mcp-spec-review.md`. Surfaces
`isError`, `structuredContent`, `ToolAnnotations`, and `_meta`
(capability_kind, inherent_labels, decision, rule, labels_added)
per the 2025-11-25 spec. Capability-driven tool visibility from
§10 means the LLM sees only tools its session can use.

**`LabeledMcpClient` (v0.2, planned)**: client-side wrapper that
hosts subprocess MCP servers (Filesystem, Fetch, Gmail, etc.) and
applies labels + policy on inbound tool results. The mirror image
of `LabeledToolClient` — same security guarantees, applied to
external MCP servers as if they were native tools. Per-server label
declaration in YAML config; per-tool argument and result rules.

### 10.8 Memory Spaces
- Implemented as a **CapableDeputy-native MCP server** (`memory.read` / `memory.write`).
- Each value stored with its label set.
- Reads union the value's labels into the calling session.
- Writes inherit the calling session's label set.
- Memory spaces are named, persistent across sessions, and can themselves carry inherent labels (e.g., a `health-notes` space defaults to `confidential.health`).

### 10.9 Audit Log
- **Append-only JSONL** at `$XDG_DATA_HOME/capabledeputy/audit.jsonl`.
- Records all events from the taxonomy in §9.2, plus session and approval lifecycle events.
- Queryable via `capdep audit` with filters; raw file is `jq`-compatible.
- File integrity: nightly hash of prior contents written into the next day's first record (tamper-evident; planned).
- Not LLM-writable: events are written by daemon components, never by content the LLM produces.

### 10.10 Mode Dispatcher (v0.1)
`select_mode(label_set, registry)` is called at the start of each
agent turn and returns one of `{TURN_LEVEL, DUAL_LLM, PROGRAMMATIC}`
with a human-readable reason. Default is `TURN_LEVEL`; auto-escalates
to `DUAL_LLM` when the session carries any `confidential.*` label and
the registry has at least one tool in the `quarantined.*` namespace.
`PROGRAMMATIC` is reserved for v0.3.

When `DUAL_LLM` is active, `build_tool_descriptions` filters the
LLM-visible tool set: raw labeled-data readers (memory.read, fs.read,
web.fetch) are hidden so the planner LLM physically cannot ask for
raw labeled bytes — only the schema-validated extractors.

The choice is logged as a `mode.selected` audit event so the trace
explains why each turn ran in the mode it did.

### 10.11 Approval System (v0.1)
Three-layer composition (DESIGN.md §8):

- **`ApprovalRequest`** — frozen dataclass with verbatim payload, in/out
  labels, action, recipient, justification, monotonic id, audit_id.
- **`ApprovalQueue`** — submit / approve / deny / defer with full
  audit emission. Cross-session declassification path: approving
  SEND_EMAIL spawns a fresh purpose-limited session, grants a one-
  shot capability scoped exactly to the approved payload + recipient,
  dispatches the email through `LabeledToolClient`, and aborts the
  session. The originating session never gains the egress capability.
- **`ApprovalPatternRule`** — auto-approves matching future requests
  with strict pattern validation (rejects bare `*`, requires domain
  anchors for globs, caps TTL at 30 days). Every match still emits
  `approval.requested` + `approval.approved` events. Revocable.

### 10.12 Quarantined LLM Extractor (v0.1)
DESIGN.md §5.2's data plane. `quarantined/extractor.extract(llm,
schema_name, labeled_text)`:

- Builds a system+user prompt with the Pydantic schema definition.
- Calls the LLM with **no tools**.
- Rejects `tool_calls` in the response (defensive — the LLM shouldn't
  have any anyway).
- Strips markdown fences from output.
- Validates via Pydantic; raises `ExtractionError` on any deviation.

Schemas live in `quarantined/schemas.py` with bounded free-text fields
to limit smuggling. Starter set: `DoseSummary`, `FinancialSummary`,
`ContactInfo`. Surfaces through the `quarantined.extract` tool which
returns NO additional_labels — schema validation IS the
declassification gate.

## 11. Tech Stack Summary

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12+ | Matches CaMeL/Dromedary/MCP ecosystem; LLM fluency in generated DSL |
| Concurrency | `anyio` | Structured, async-native, works with both asyncio and trio |
| CLI | Typer | Type-hinted, modern, builds on Click |
| TUI | Textual | Multi-pane, mature, terminal-native |
| Output | Rich | Coloring, tables, panels |
| LLM SDKs | LiteLLM (unified) | Provider-agnostic from day one |
| Tool protocol | MCP | Ecosystem standard, works with OpenClaw skills via adapter |
| DSL | Starlark (forked) | Deterministic, hermetic, Python-syntactic |
| Policy | OPA (Rego) | Mature, declarative, auditable |
| State | SQLite | Zero-ops, deterministic, easy to back up |
| Test framework | pytest + Hypothesis | Property-based testing for the policy engine |
| LLM record/replay | `vcr.py`-style cassettes | Deterministic test replay |
| License | Apache 2.0 | Permissive, commercial-friendly, the right move for security infra |

## 12. Testing Strategy

### Coverage targets
- Policy engine, interpreter, session graph: **>95%**.
- LLM adapter layer, MCP client layer, trace pipeline: **>85%**.
- CLI/TUI: **>70%** (UI surface partially exempt).

### Test types
- **Unit tests** (pytest): every pure function.
- **Property-based tests** (Hypothesis): policy decisions are total functions; session graph maintains invariants under arbitrary operation sequences; label propagation is monotonic.
- **Integration tests**: agent loop with stubbed MCP servers and replayed LLM cassettes.
- **Trace fixtures as regression tests**: a known-good trace pinned as a fixture; replaying it against a new policy version must produce the same decision unless the policy change is deliberate.
- **End-to-end scenarios**: the canonical four scenarios listed in §13.
- **Adversarial tests**: corpus of known prompt-injection payloads (drawn from AgentDojo and similar benchmarks) verified to fail to violate policy.

### Test fixtures
- LLM cassettes captured against real APIs once, replayed deterministically.
- Stub MCP servers respond from YAML-defined fixtures.
- Approval decisions in tests are scripted via `capdep approve --auto-from <fixture>`.
- Trace-replay fixtures generated from real sessions during development.

## 13. Canonical Demo Scenarios

These four scenarios serve as both end-to-end tests and demo material:

1. **Prescription-to-wife** — health-context session reads PHI; comms-context session has email; cross-session declassification gated by approval; one-shot capability scoped to specific recipient and exact payload.
2. **Untrusted-email-tries-to-purchase** — inbound email from third party suggests buying something; agent constructs purchase intent; policy rejects auto-execution; queued for human approval; approval shows the email and the purchase as a single decision.
3. **Parallel research with eventual join** — web-research session (untrusted-external) running parallel to a personal-notes session (confidential.personal); user explicitly merges with declassification of summary text only.
4. **Programmatic dry-run** — user gives complex multi-step task; planner emits Starlark; user reviews dry-run output; approves; execution proceeds with full label propagation.

Each scenario must produce a clean trace inspectable via `capdep trace`.

## 14. Operational Model

- **Single-user deployment first.** Multi-tenant is out of scope for v0.1.
- Daemon runs in tmux for development; `systemd --user` unit for daily use.
- All client-daemon communication local-only (Unix socket); no network listeners.
- Configuration via YAML at `$XDG_CONFIG_HOME/capdep/config.yaml`.
- Audit log rotated daily; retention configurable.
- **Backup strategy**: `state.db` and `audit.jsonl` are the persistence; both are file-level backupable.
- **Recovery**: daemon crash recovery from SQLite state; partial sessions resume from last persisted turn.
- **Remote operation**: SSH to the host, run terminal clients there. No web UI, no mobile app, no remote daemon listener.
- **Container-deployable.** All paths are env-overridable (`CAPDEP_SOCKET`, `CAPDEP_STATE_DB`, `CAPDEP_AUDIT_LOG`, `CAPDEP_DATA_DIR`) so container deployment is a configuration change, not a refactor. v0.2 ships the Containerfile and quadlet (ROADMAP.md); v0.1 keeps the architecture ready.

## 15. Open Questions / Future Work

### Shipped in v0.2 (DONE)

- **MCP Resources for memory entries** — `capdep://memory/{key}` with
  labels in `_meta`; reads dispatch through `LabeledToolClient`.
- **MCP Prompts for canonical workflows** — starter set:
  prescription-review, daily-briefing, safe-share, untrusted-research.
- **MCP Elicitation for in-flow approvals** — host shows confirmation
  modal; approval submitted + executed in a one-shot session inline.
- **MCP Logging notifications** — policy decisions and label
  propagations mirrored as `notifications/message`.
- **MCP `tools/list_changed` notifications** — daemon-side
  capability.granted audit event triggers MCP push.
- **`LabeledMcpAdapter` + `UpstreamManager`** — wrap subprocess MCP
  servers (Filesystem, Fetch, Gmail, etc.); foundation in
  `src/capabledeputy/upstream/`.
- **TUI five-pane layout** — Sessions / Approvals / Conversation /
  Trace / Events; real-time event subscription replaces polling.
- **Container deployment** — Containerfile, systemd quadlet,
  documented volume layout; rootless uid 1500.
- **Daemon subscription primitive** — `subscribe`/`unsubscribe`
  JSON-RPC methods + `Daemon.publish(stream, payload)` server-push.

### Shipped or in progress for v0.3

- **Programmatic execution mode (§5.3, §10.5)** — Python-AST-subset
  interpreter with label-aware values and static policy analyzer.
  Surfaced as `capdep run` / `capdep dry-run`.
- **Per-session unforgeable tool tokens** — strict object-capability
  semantics for tool names. Each session sees capabilities under
  fresh random tokens; the LLM cannot reference tools outside its
  compartment because it doesn't know their session-specific names.
  Phase 7b's capability-driven visibility filter covers ~95% of the
  architectural benefit; the token aliasing is defense-in-depth and
  earns provable separation properties in formal verification. PLANNED.
- **`SKILL.md` adapter** for ingesting OpenClaw skills as labeled MCP
  tools. PLANNED.
- **Local-model planner option** — keep the privileged LLM local
  (Ollama, llama.cpp), only send non-labeled handles to a frontier
  model. LiteLLM already supports the underlying providers; this
  reduces to a documentation + sample-config piece.

### Planned for v0.4+

- **Per-tool container isolation** — beyond v0.2's all-in-one
  container, run each MCP server in its own container with
  policy-driven network and filesystem views. Strongest blast-radius
  containment; significant operational complexity.
- **Multi-tenant labels** — CapableDeputy as a household tool with
  per-person label spaces.
- **Inter-host federation** — running CapableDeputy on a phone and a
  laptop with shared state. Significant design work.

### Planned for v0.5+

- **Formal verification** — property-based tests get us most of the
  way; full TLA+ specification of the session graph and policy
  semantics is desirable but post-v0.1.
- **Mechanized proofs** of key safety properties (label monotonicity,
  capability unforgeability).
- **Independent security audit.**

### Strategic / opportunistic

- **Engagement with OpenClaw RFC #39160** — once v0.1 demos cleanly,
  propose CapableDeputy as the answer to the open RFC. Not a
  dependency, but strategic.
- **MCP RFC for capability-aware tool exposure** — a per-tool
  `requiredCapability` annotation in the MCP spec would let
  capability-aware hosts (CapableDeputy and others) filter at
  `tools/list` time. Reference implementation exists in our MCP
  server's `_meta` (under `io.capabledeputy/capability_kind`); could
  be promoted to a standardized namespace. See `docs/mcp-spec-review.md`.

## 16. Naming and Identity

- **Project name:** CapableDeputy.
- **Daemon binary:** `capdep`.
- **Tagline:** *"A capable deputy, never a confused one."*
- **License:** Apache 2.0.
- **Repository:** to be created.

## 17. Implementation Status (v0.1)

Phases 0–7 of ROADMAP.md are complete and verified end-to-end:

| Built and verified | Where |
|---|---|
| Daemon, JSON-RPC over Unix socket, CLI shell | §10.1, §10.2 |
| Daemon subscription primitive (publish/subscribe over JSON-RPC) | §10.1 |
| Session graph with fork/pause/resume/abort + SQLite persistence | §6, §10.8 |
| Label + Capability + Action types; pure-Python policy engine | §7, §10.4 |
| LabeledToolClient + native tools (memory, purchase, email, extract) | §10.7 |
| Turn-level agent loop + LiteLLMClient + ClaudeCodeLLMClient + FakeLLMClient | §5.1, §10.6 |
| Approval queue, cross-session declassification, pattern rules | §8, §10.11 |
| Quarantined LLM extractor (dual-LLM mode) + schemas | §5.2, §10.12 |
| Mode dispatcher with auto-escalation + capability-driven tool visibility | §5.4, §10.10 |
| Trace event taxonomy + audit log + `capdep trace` / `capdep audit` | §9 |
| MCP server — tools, resources, prompts, elicitation, logging, list_changed | §10.7 |
| Upstream MCP wrapping foundation (`LabeledMcpAdapter`, `UpstreamManager`) | §10.7 |
| TUI five-pane layout with real-time event push + session-graph toggle | §10.3 |
| Container deployment (Containerfile + quadlet + docs) | §14 |
| Programmatic mode interpreter + static analyzer + `capdep run` / `capdep dry-run` | §5.3, §10.5 |
| 336+ unit tests + 2 real-LLM integration tests | §12 |

See ROADMAP.md for v0.2 / v0.3 / v0.4 plans, including the MCP
Resources / Prompts / Elicitation expansion, programmatic mode,
upstream MCP server wrapping, and container deployment.

---

See ROADMAP.md for the phased implementation plan and current status.
