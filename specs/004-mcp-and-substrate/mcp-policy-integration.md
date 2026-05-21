# MCP Policy Integration — Deep Design

**Companion to** [`mcp-protocol-fit.md`](./mcp-protocol-fit.md) and
[`programmatic-policy-primitives.md`](./programmatic-policy-primitives.md).
**Audience:** architect / implementer / operator deciding how third-
party MCP servers map onto CapableDeputy's policy engine.

**Note:** the consolidated reference for the policy language and
the three programmatic primitives (RaiseOnlyInspector,
DecisionInspector, DeclassifyingTransformer) lives in
[`programmatic-policy-primitives.md`](./programmatic-policy-primitives.md).
This document focuses on the MCP-specific design positions and
references the primitives doc for primitive definitions.

**Updated positions** (from later conversation rounds):

1. **Sampling** is supported by default for operator-curated servers;
   routed through the quarantined LLM is OPTIONAL (chokepoint mediates
   tool calls anyway). `sampling.tools` requires per-server enable
   with an exposed-tool-subset config.
2. **Elicitation form mode** integrated into the existing approval
   queue (new `ApprovalAction.ELICITATION_RESPOND`). URL mode for
   OAuth flows with the flow-pattern-session model (§3.7).
3. **Prompts** auto-forward for operator-curated servers when tagged
   `io.joneslaw/capabilitydeputy/safe_to_forward`.
4. **`resources/updated` push notifications** are proxied through the
   chokepoint as synthesized `resources/read` actions — not refused.
5. **Namespace** is `io.joneslaw/capabilitydeputy/*` (MCP `_meta`
   reverse-DNS convention).
6. **Incoming data is labeled (not approval-gated); outgoing data
   is approval-gated based on session + per-arg payload labels.**
   This is the asymmetry that makes MCP integration practical.
7. **Inspectors, decision inspectors, and declassifiers** are the
   three programmatic primitives. See
   [`programmatic-policy-primitives.md`](./programmatic-policy-primitives.md)
   for definitions, hook list, and composition rules.

This document answers the question:
**given the protocol-fit positions, how do labels, flow patterns,
security models, approval-bundling, and operator dial all integrate
at the MCP layer — for any third-party server, configurable, with
the option for tight per-server policy?**

The driving design principles, in priority order:

1. **Operator authority is paramount.** Every meaningful authority
   delegation is operator-explicit; the agent never escalates.
2. **The chokepoint is `engine.decide()`.** Every MCP-mediated action
   passes through it. There is no out-of-band path.
3. **Minimize approval fatigue.** Bundle approvals across actions;
   apply FR-034 optimistic-auto wherever reversible/system + non-
   egressing holds; use the envelope dial.
4. **Performance matters.** MCP IPC is ~ms; in-process `decide()` is
   ~µs. Don't make every microservice call N IPC roundtrips when
   one will do.
5. **Tight integration is operator-opt-in.** A CapableDeputy-aware
   server can declare richer policy hints via the `cd:` annotation
   namespace; operators choose whether to honor them.

---

## 1. Trust tiers — what "operator-curated" means

The `trust_tier` config field on each upstream server slot governs
how much of the server's self-description we honor.

| Tier | Annotation trust | Heuristic | Per-tool override | Use case |
|---|---|---|---|---|
| `unvetted` | Ignored entirely | Sole signal; strict=True; unmapped tools refused | Required for any non-trivial tool | Random server an operator is evaluating |
| **`operator-curated`** | **Honored when present + consistent with heuristic** | Backup signal; `cd:` namespace honored | Optional per-tool refinement | **Default**: operator-reviewed manifest |
| `vendor-vetted` | Authoritative when present; `cd:` namespace authoritative | Used only as a sanity check (warn on disagreement) | Rare | Anthropic reference, vendor-signed server |

**Operator-curated** behavior in detail:

- The heuristic still runs.
- If the heuristic and the server's annotations *agree* on capability kind
  (`READ_FS`, `WRITE_FS`, etc.) → register without warning.
- If they *disagree* in a "stricter" direction (heuristic says `READ_FS`,
  server says `destructiveHint=true`) → **refuse to register; emit
  `MCP_HEURISTIC_DISAGREEMENT_REFUSED`**. The operator must add an
  explicit per-tool override.
- If they disagree in a "looser" direction (heuristic says `DELETE_FS`,
  server claims `readOnlyHint=true`) → **honor the heuristic; emit
  `MCP_ANNOTATION_OVERRIDDEN` warning**. Safer default.
- The `cd:` annotation namespace (see §3.1) is honored if present.

**Per-server config example:**

```yaml
upstream_servers:
  - id: notion-mcp
    transport: stdio
    command: ["mcp-server-notion"]
    trust_tier: operator-curated
    risk_preference: balanced
    flow_pattern_default: pattern_2_dual_llm
    default_category: work
    default_tier: sensitive
    tool_overrides:
      create_page:
        capability_kind: CREATE_FS
        effect_class: data.create_remote
        default_reversibility: {degree: reversible, agent: system}
      delete_page:
        capability_kind: DELETE_FS
        effect_class: data.delete_remote
        social_commitment: false  # not an external send
```

---

## 2. Per-surface design

Each surface is analyzed across (flow pattern × security model)
combinations. The patterns are:

- **Pattern ① turn-level** — agent picks tools and calls them
- **Pattern ② DUAL_LLM** — quarantined LLM mediates untrusted content
- **Pattern ③ ReferenceHandle** — opaque handles for data-blind planning
- **Pattern ④ Programmatic** — bundle-execute against a hashed program
- **Pattern ⑤ Sandbox** — substrate-isolated execution

The security models are:
- Brewer-Nash conflict rules
- FR-008 Bell-LaPadula clearance
- FR-019 reversibility + social-commitment
- FR-025 raise-only inspector
- FR-034 optimistic-auto carve-out
- FR-036 distinct-attester override

### 2.1 Tools

| Pattern × Model | Behavior |
|---|---|
| ① × Brewer-Nash | Standard. Each `tools/call` runs through `decide()`. Cap match + label compose. |
| ① × FR-019 | Tool's `cd:default_reversibility` (or heuristic-inferred) drives the gate. Reversible/system + non-egressing → AUTO. |
| ① × FR-034 | A server's `readOnlyHint=true` + non-egressing → operator dial decides whether to AUTO or REQUIRE_APPROVAL. |
| ② × Brewer-Nash | Tool's output (returned content blocks) flows through `quarantined.extract` if the operator-configured flow pattern for that tool is `pattern_2`. Schema-validated fields land on the orchestrator; raw content stays in the quarantined LLM. |
| ② × FR-025 | The inspector runs on the tool's raw output BEFORE the dual-LLM extraction. Detected injection markers raise the session's taint. |
| ③ × any | Tool args declared as `cd:handle_arg_names` are bound at dispatch time. The planner passes opaque UUIDs; the dispatcher resolves to real values AFTER `decide()` approves. |
| ④ × Brewer-Nash | Bundle dry-run collects all gates from a workflow that includes `tools/call`s. The chokepoint runs on each step in the dry-run with synthetic results. Operator approves the bundle as a unit. |
| ⑤ × any | If the server is itself a sandboxed-substrate provider (e.g., a Pyodide MCP server), the upstream-isolation work (podman/docker) takes care of process boundary; we additionally apply standard `decide()` to each tool call. |

**Approval-fatigue strategy:**

- **Per-server standing caps.** Operator config grants e.g. `READ_FS *`
  on the Notion server. Reads don't prompt.
- **Bundle support.** A workflow that calls N tools on the same server
  becomes one approval via `dry_run_for_bundle`. The MCP roundtrip
  cost is paid N times but the operator-attention cost is paid once.
- **Optimistic-auto for the obvious cases.** Reversible-list / read
  tools tagged `readOnlyHint=true` get FR-034 carve-out by default
  on `operator-curated` servers.
- **Envelope dial per-server.** Operator config `risk_preference:
  balanced` applies to all that server's tools as the default cell;
  per-tool overrides as needed.

**Performance strategy:**

- **Cache `tools/list`.** Invalidate on `notifications/tools/list_changed`
  or on operator request. The list is content-addressable.
- **Pre-bind the cap-match.** When the adapter registers a tool, the
  CapabilityKind is fixed at registration time. The cap match is
  cheap.
- **Pipeline the decide-then-call.** For a sequence of tool calls
  with no inter-step dependencies, pipeline the IPC: send all the
  decide-approvals concurrently, then send all the MCP calls in
  parallel.
- **Single-shot operations.** When the planner is going to call N
  tools, prefer a tool with N-batch semantics if the server exposes
  one. `cd:batch_kind` annotation (see §3.1) signals this.

**Deep integration:**

- The `cd:` namespace lets a server self-describe its policy fields
  (effect_class, reversibility, social_commitment, flow_pattern_preferred,
  category_hint, tier_hint). See §3.1.
- A per-server policy module (Python, §3.2) can override the simple
  mapping with conditional logic ("if args.path matches /docs/, use
  category=docs; if /finance/, use category=finance").

### 2.2 Sampling

**Default position: refused. Period.**

Even with operator-curated trust, sampling is the surface where the
server directs our LLM. That's the core attack we exist to mediate.

| Pattern × Model | Behavior |
|---|---|
| any × any | Refuse. Audit `MCP_SAMPLING_REFUSED` with server id + request id. |

**Future option (not implemented; documented for completeness):**

If an operator legitimately needs sampling for a specific server,
the path is to wrap it in a Pattern ② DUAL_LLM:

1. Server requests `sampling/createMessage` with a prompt.
2. The adapter routes the prompt through the quarantined LLM with
   an operator-defined schema.
3. The quarantined LLM produces schema-validated output.
4. The server receives the schema-bounded answer.

The orchestrator never sees the server's prompt; the server never
directs the orchestrator. Pattern ② is the structural mediation.

This is opt-in PER SERVER and PER FLOW. The default remains refused.

### 2.3 Elicitation

**Default position: refused. Opt-in per server later.**

| Pattern × Model | Behavior |
|---|---|
| any × any (default) | Refuse. Audit `MCP_ELICITATION_REFUSED`. |
| Future: ① form × any | After operator-explicit per-server enable, form requests rendered in TUI; bundled when multiple within same server-session window. |
| Future: any × ③ | Operator's response could be wrapped in a ReferenceHandle so the server sees a handle, not the raw input — useful for sensitive elicitations even in `form` mode. |
| Future: URL mode | OAuth flow per §3.7 — flow-pattern-session-scoped tokens. |

**Approval-fatigue strategy (when enabled):**

- **Bundle elicitations within a server-session window.** If a server
  asks for {region, language, currency} in quick succession, render
  them as ONE form for the operator.
- **Per-session cache.** Once the operator answers "language = en-US"
  for this session, subsequent elicitations from the same server
  for the same field auto-populate.
- **Operator pre-fills.** Common answers (GitHub username, default
  region) live in operator config; elicitations matching pre-fill
  schemas resolve without UI.

**Performance:** elicitation is human-paced; latency dominates.

**Deep integration:**

- A server's elicitation schema could declare `cd:bundleable_with: ["region"]`
  to hint at grouping with other elicitations from the same server.
- A `cd:operator_prefill: profile.github_username` reads from operator
  config and skips the prompt.

### 2.4 Resources

`resources/list`, `resources/read`, and the embedded-resource content
blocks that flow inside tool results.

| Pattern × Model | Behavior |
|---|---|
| ① × Brewer-Nash | `resources/read` is a `READ_FS`-capability action. Returned content tagged with the server's category/tier (operator config) PLUS default `UNTRUSTED_EXTERNAL`. |
| ② × any | A resource read can be routed through `quarantined.extract` when the URI matches a configured Pattern ② mapping (operator declares "any `notion://*` URI extracts as `MeetingNote` schema"). |
| ③ × any | A resource URI can BE a handle. The operator binds an opaque ID to a real URI; planner asks for "the resource the operator named X" without seeing the URI. |
| ④ × any | A bundle that reads N resources gets one approval gate per read (or one bundled gate if they're on the same server with standing caps). |
| ⑤ × any | Resources from sandboxed substrate servers are bound to the sandbox's namespace. |

**Approval-fatigue strategy:**

- **Per-server URI BindingSet.** Operator config declares "Notion's
  `notion://*` URIs are category=work, tier=sensitive, reversibility=
  reversible". Reads against the server fall under standing caps.
- **Resource list is free.** `resources/list` returns metadata only;
  it's the equivalent of `ls`. Treat it as a read of trivial cost.
- **Read bundles.** A bundle can collect N reads from the same server.
  The operator sees "approve reading these 5 URIs from Notion" as
  one impact tree.

**Performance:**

- **Cache reads.** With `lastModified` annotation honored, the
  adapter can serve stale-while-revalidate. Operator config sets
  TTL per server.
- **Resource list cache.** Invalidate on `notifications/resources/
  list_changed`. Don't auto-refetch — wait for the next operator
  interaction.
- **Embedded resources from tool returns.** Don't re-fetch; the
  embedded content arrived with the tool result. Tag it and store.

**Deep integration:**

- `cd:category_hint` and `cd:tier_hint` on resource annotations let
  a CapableDeputy-aware server declare its compartment shape.
- `cd:pattern_2_schema: MeetingNote` on a resource auto-routes reads
  through quarantined extraction with that schema.

**The subscribe NO.** `resources/subscribe` is refused by default.
Subscriptions push out-of-band of `decide()`, breaking the chokepoint.
If a server needs to advertise updates, it should send `list_changed`
and let the operator's next interaction trigger a re-read.

### 2.5 Prompts

`prompts/list` + `prompts/get`. Slash-command-style operator-initiated.

| Pattern × Model | Behavior |
|---|---|
| ① × Brewer-Nash | Operator invokes `/promptname`. The returned messages are shown in TUI. Operator clicks "forward to LLM" to inject; the messages then flow as `UNTRUSTED_EXTERNAL`. |
| ② × any | A prompt that returns lots of embedded content can be routed through `quarantined.extract` per operator config. Useful for "summarize this server's docs" prompts. |
| ④ × any | A prompt's intent can be the SOURCE of a programmatic bundle: the operator parses the prompt's tool-call hints into a program, dry-runs, approves. |
| any × FR-013 | Prompt content carries no fork-inheritance properties; it's read-once on the receiving session. |

**Approval-fatigue strategy:**

- **Operator-explicit forward.** Prompts don't auto-inject. The
  operator clicks "use this prompt" in the TUI. That click counts
  as one approval; no additional gate fires for the prompt content
  itself.
- **Operator-ratified prompts** (per spec 005 V024-V029 rituals) become
  saved rituals — running them later doesn't re-prompt for the
  template content.
- **Template caching.** Cache by content hash; subsequent invocations
  with the same args don't refetch from the server.

**Performance:**

- Prompts are typically small (~1KB). Cache aggressively.
- Argument substitution happens server-side; the adapter just
  receives the rendered messages.

**Deep integration:**

- `cd:flow_pattern_preferred: pattern_2` on a prompt routes it through
  quarantined extraction.
- `cd:operator_ratifiable: true` exposes the prompt as a saveable
  ritual in the operator's TUI.

### 2.6 Roots

| Pattern × Model | Behavior |
|---|---|
| any × any (default) | `roots` capability NOT declared. Untrusted servers can't see our workspace layout. |
| any × any (per-server enable) | For an `operator-curated` server, optionally declare a scoped roots list. The roots are the BindingSet entries for THAT server's category — nothing more. |

**Approval-fatigue strategy:** roots are config-time, not run-time.
No per-call approval needed.

**Performance:** trivial. List is tiny; cache it; invalidate on
operator config change.

**Deep integration:** the `roots` declaration is *projected* from the
operator's BindingSet:

```yaml
upstream_servers:
  - id: filesystem-mcp
    roots_from_bindings: ["work/*", "personal/notes/*"]
```

The adapter computes the file:// URIs for matching bindings and
returns those as roots. The server's view of "the workspace" is
exactly what the operator declared in their bindings.

### 2.7 Notifications

| Notification | Action | Performance |
|---|---|---|
| `tools/list_changed` | Mark cache stale; re-list on next operator interaction | Don't re-fetch immediately (would thrash). |
| `resources/list_changed` | Same as tools | Same |
| `prompts/list_changed` | Same as tools | Same |
| `resources/updated` | **Refuse / ignore.** Optional: surface a TUI hint that the resource changed; operator polls if interested. | Out-of-band push has no chokepoint hook. |
| `elicitation/complete` | Only relevant if elicitation declared; audit + (optional) auto-resume the original request. | Coalesce duplicate completions. |
| `progress` | Audit + TUI progress bar. Reset request timeout per spec. | Sample (don't display every percent). |
| `cancelled` | Propagate to in-flight tool call. | Free; just a flag flip. |
| `message` (logging) | Audit; surface to TUI if `info` or higher. | Rate-limit by server. |

---

## 3. Cross-cutting design

### 3.1 The `cd:` annotation namespace

A CapableDeputy-aware MCP server can declare richer policy hints
using the `cd:` annotation namespace on tools, resources, and
prompts. These hints are advisory — the operator's config can
override — but they let a vendor ship policy alongside their server
manifest.

**Per-tool annotations:**

| Annotation | Type | Effect |
|---|---|---|
| `cd:effect_class` | string | Maps to `ToolDefinition.effect_class`. |
| `cd:default_reversibility` | `{degree, agent}` | Maps to `default_reversibility`. |
| `cd:social_commitment` | bool | Maps to `social_commitment`. |
| `cd:category_hint` | string | Suggested label category. |
| `cd:tier_hint` | string | Suggested tier (`none`/`sensitive`/`regulated`/`restricted`). |
| `cd:flow_pattern_preferred` | enum | `pattern_1`/`pattern_2`/`pattern_3`/`pattern_4`/`pattern_5`. |
| `cd:handle_arg_names` | `[string]` | Args that should be ReferenceHandle-bound. |
| `cd:batch_kind` | string | When multiple calls would happen, group by this batch_kind (one approval for the group). |
| `cd:idempotent` | bool | Beyond `idempotentHint` — declares retry-safe semantics for the dispatcher. |
| `cd:operator_ratifiable` | bool | Tool may be saved as a ritual. |

**Per-resource annotations:**

| Annotation | Effect |
|---|---|
| `cd:category_hint` | Compartment category. |
| `cd:tier_hint` | Sensitivity tier. |
| `cd:reversibility_hint` | Reversibility class. |
| `cd:pattern_2_schema` | Quarantined-extract schema name. |
| `cd:retention_hint_seconds` | Suggested cache TTL. |

**Per-prompt annotations:**

| Annotation | Effect |
|---|---|
| `cd:flow_pattern_preferred` | As above. |
| `cd:operator_ratifiable` | Saveable as ritual. |
| `cd:embeds_untrusted_content` | Bool; if true, taint the session on forward. |

**Per-elicitation annotations** (when enabled):

| Annotation | Effect |
|---|---|
| `cd:bundleable_with` | List of other elicitation names to group with. |
| `cd:operator_prefill` | Config-path to read default from. |

**Trust gating:** `cd:` annotations are honored according to the
server's `trust_tier`:

- `unvetted`: ignored entirely
- **`operator-curated` (default): honored when consistent with the
  per-tool override or with the heuristic. Disagreement triggers
  registration refusal or warning, per §1.**
- `vendor-vetted`: authoritative.

### 3.2 Per-server policy modules

Per-server YAML config covers the common cases. For richer logic, an
operator may attach a Python policy module to a server:

```python
# configs/upstream_policies/notion.py
from capabledeputy.upstream.policy_api import ServerPolicy, ToolMapping

class NotionPolicy(ServerPolicy):
    server_id = "notion-mcp"

    def map_tool(self, mcp_tool: dict) -> ToolMapping | None:
        if mcp_tool["name"] == "create_page":
            return ToolMapping(
                capability_kind="CREATE_FS",
                effect_class="data.create_remote",
                default_reversibility=("reversible", "system"),
                inherent_labels={"work"},
            )
        if mcp_tool["name"] == "search":
            # Conditional logic: search arg determines category
            return ToolMapping(
                capability_kind="READ_FS",
                inherent_labels_fn=lambda args: (
                    {"finance"} if "finance" in args.get("query", "").lower()
                    else {"work"}
                ),
            )
        return None  # fall through to heuristic + cd: annotations

    def label_propagation(
        self, mcp_tool: dict, args: dict, result_content: list,
    ) -> frozenset[str]:
        """Override the default UNTRUSTED_EXTERNAL tag for some tools."""
        if mcp_tool["name"] == "get_my_profile":
            return frozenset({"confidential.personal", "principal-direct"})
        return frozenset({"untrusted.external"})
```

The module is loaded by the adapter; tools/resources/prompts are first
checked against the policy module, then YAML overrides, then `cd:`
annotations, then heuristic. First match wins.

**Why this matters:** for popular servers (GitHub, Notion, Slack,
Gmail) the community can ship policy modules that capture the
server's specific compartment + reversibility shape, so operators
don't have to write per-tool overrides from scratch.

### 3.3 Per-server envelope dial config

The risk-preference dial (FR-030) can be set per-server, with the
operator-global value as fallback:

```yaml
upstream_servers:
  - id: notion-mcp
    risk_preference: balanced
  - id: github-mcp
    risk_preference: cautious  # more friction on this server
  - id: my-local-script
    risk_preference: permissive  # I wrote it, trust it
```

This lets the operator tune autonomy per-server without rewriting
policy. The hard floors (SC-010) remain unmovable; the dial only
steers within declared cell envelopes.

### 3.4 Approval bundling across MCP surfaces

The existing approval bundle (`programmatic.bundle_runner`) collects
gates from a Python-AST-subset workflow. With MCP surfaces wired in,
a single bundle can include:

- `resources/read` calls (collect approval per resource OR per server
  with standing caps)
- `tools/call` invocations (one gate per call, or one bundle gate
  for tools tagged `cd:batch_kind="X"`)
- `prompts/get` followed by `tools/call`s the prompt suggests
- Elicitations (when enabled): each form rendered once; collected
  with other elicitations in the same window

**Example bundle source:**

```python
# A "monthly Notion + GitHub summary" workflow.
notes = call("notion.search", query="this month")
prs = call("github.list_prs", state="merged", since="2026-05-01")
draft = call("memory.create",
    key="monthly-summary",
    value=f"Notes: {len(notes)}, PRs merged: {len(prs)}",
)
sent = call("email.send",
    to="boss@example.com",
    subject="Monthly summary",
    body="see memory.monthly-summary",
)
```

The bundle's gates list shows: `notion.search` (auto under standing
cap), `github.list_prs` (auto), `memory.create` (optimistic), and
`email.send` (REQUIRE_APPROVAL — financial label, social commitment).
Operator approves the ONE non-auto gate and the bundle executes
without further interaction.

### 3.5 Optimistic-auto applied to MCP

FR-034 optimistic-auto carve-out applies cleanly to MCP tool calls
when:

1. The tool's effective reversibility is reversible/system
2. The effect class is non-egressing
3. No Brewer-Nash conflict on the session

For `operator-curated` servers, the adapter checks the server's
`readOnlyHint` AND `cd:default_reversibility` AND the heuristic. If
all three agree on reversible/system, the carve-out fires without
prompting.

Concretely:

| Tool kind | Likely outcome under permissive dial |
|---|---|
| Notion `search` | AUTO (read-only, system-revocable, no egress) |
| Notion `read_page` | AUTO |
| Notion `create_page` | AUTO if op-curated permits; else REQUIRE_APPROVAL |
| Notion `delete_page` | REQUIRE_APPROVAL or DENY (irreversible/external) |
| GitHub `list_issues` | AUTO |
| GitHub `create_issue` | AUTO if op permits |
| GitHub `merge_pr` | REQUIRE_APPROVAL (social-commitment-adjacent) |

This is **the** approval-fatigue answer: most read-only operations
go through with no prompts. Operator attention is reserved for
state-modifying egress + irreversibility.

### 3.6 Caching strategy

| What | Where | Invalidation |
|---|---|---|
| `tools/list` per server | adapter-level | `list_changed` notification OR operator-triggered refresh |
| `resources/list` per server | adapter-level | `list_changed` OR TTL |
| `resources/read` content | per-resource-uri | `lastModified` annotation OR TTL OR explicit refresh |
| `prompts/list` per server | adapter-level | `list_changed` |
| `prompts/get` rendered | content-addressed by (name, args) | implicit immutability (same input → same output) |
| OAuth tokens | per (server_id, flow-pattern-session) | session-end OR token expiry |
| Heuristic cap-kind mappings | per (server_id, tool_name) | server config change |

Cache hits are critical because MCP IPC dominates latency. A typical
inbox-triage workflow could see 10× speedup with a hot cache.

### 3.7 OAuth flow-pattern-session model

Per the operator decision: **OAuth tokens are per-server, scoped to a
flow-pattern-session.** This is more conservative than the typical
web-app model (token stored indefinitely until revoked).

**Definitions:**

- **Flow-pattern session** = a CapableDeputy session whose `purpose_handle`
  AND `axis_d.initiator` are stable. A session that the operator
  spawned for "research" runs one flow-pattern-session. A subsequent
  session for "writing" is a different flow-pattern-session.
- **Server slot** = a per-server identity in `upstream_servers` config.

**Token lifecycle:**

1. Operator initiates session S1 with purpose=research, initiator=alice.
2. Tool call to `notion-mcp` triggers OAuth requirement (URL
   elicitation per §2.3).
3. Operator opens the URL, authorizes Notion.
4. Token T1 is stored in the daemon's private credential store,
   indexed by (notion-mcp, S1.purpose_handle, S1.axis_d.initiator).
5. Subsequent tool calls in S1 reuse T1 from cache.
6. Session S1 ends OR purpose_handle changes → T1 moves to "escrow".
7. If a new session S2 with the SAME (purpose_handle, initiator) runs:
   the operator is prompted "use the escrowed Notion token from your
   previous research session, or re-authorize?" Operator chooses.
8. If a new session S3 with a DIFFERENT (purpose_handle, initiator) runs:
   T1 is NOT offered. Operator must re-authorize.

**Properties:**

- A token never crosses purpose-handle boundaries silently.
- A token never crosses operator identity (though we're single-user
  currently, so this is just a placeholder).
- Token theft is bounded: even if the daemon-private credential store
  is compromised, the attacker has tokens scoped to specific purpose-
  handles, not blanket access.
- Re-authorization is an explicit operator action (URL elicitation
  with the existing controls — full URL displayed, secure browser,
  operator consent).

**Escrow vs. discard:**

- The escrow option exists because re-authorizing every session is
  brutal UX. The operator's choice is "convenient" (escrow) vs.
  "paranoid" (discard).
- Default: 24-hour escrow with explicit operator opt-in to reuse.
- Operator config can disable escrow entirely (`oauth_escrow: false`).

**Audit trail:**

- `MCP_OAUTH_TOKEN_ISSUED` (server, session_purpose, session_initiator)
- `MCP_OAUTH_TOKEN_REUSED_FROM_ESCROW` (server, prev_session_id,
  new_session_id, operator_confirmed)
- `MCP_OAUTH_TOKEN_REFRESHED` (server, session)
- `MCP_OAUTH_TOKEN_DISCARDED` (server, session, reason)

**Storage:** existing `src/capabledeputy/secrets.py` with per-server-and-
session keying. Per-server filesystem isolation (separate file per
server, 0o600) so OS-level compromise of one server's token doesn't
expose others.

---

## 4. Implementation roadmap (updated)

Combining the P0-P2 roadmap from `mcp-protocol-fit.md` with the deep
design positions:

| Priority | Item | Effort |
|---|---|---|
| **P0** | Per-server `trust_tier` config + heuristic-disagreement audit/refusal | 1 day |
| **P0** | Refuse `sampling` and `elicitation` with audit | 0.5 days |
| **P0** | `resources/list` + `resources/read` with UNTRUSTED_EXTERNAL default + per-server BindingSet honoring | 2 days |
| **P0** | `cd:` annotation namespace honoring (effect_class, default_reversibility, category_hint, tier_hint) | 1 day |
| **P0** | Notification handlers (refuse `resources/updated`; mark-stale on list_changed) | 0.5 days |
| **P1** | `prompts/list` + `prompts/get` with operator-explicit forward UX | 2 days |
| **P1** | Embedded-resource label propagation in tool results | 1 day |
| **P1** | Per-server `risk_preference` dial config | 0.5 days |
| **P1** | Approval bundle support across MCP surfaces (verify gates work with `tools/call`, `resources/read`) | 1 day |
| **P2** | Per-server Python policy modules (the §3.2 plug-in) | 2 days |
| **P2** | Per-server scoped `roots` projection from BindingSet | 1 day |
| **P2** | `cd:flow_pattern_preferred` routing (Pattern ② for resources/prompts) | 1.5 days |
| **P2** | Caching layer (tools/list, resources/list, prompts, reads) | 1.5 days |
| **P3** | Streamable HTTP transport | 1 week |
| **P3** | OAuth flow-pattern-session model (§3.7) | 1 week |
| **P4** | Optional: elicitation form mode opt-in per server | 2 days |
| **P4** | Optional: sampling Pattern ②-wrapped (per §2.2 future option) | 1 week |

**P0 total**: ~5 days. **P0-P2 total**: ~15 days. **P3** adds HTTP + OAuth (~2 weeks).

P4 stays optional; many operators will never need elicitation or
sampling. Build them only when a concrete server requires it.

---

## 5. What this changes for spec-007

Spec-007 (stub-as-MCP integration tests) was originally going to be
"wrap our stubs as MCP servers and run them through the adapter." With
this design in hand, spec-007 should additionally:

1. **Validate the trust_tier flow.** Test that `operator-curated`
   tier honors `cd:` annotations correctly + refuses on disagreement.
2. **Validate the cd: annotation honoring.** A stub MCP server
   declares full `cd:` annotations; verify the adapter maps them
   to a proper `ToolDefinition`.
3. **Validate notification refusal.** A stub sends
   `resources/updated`; verify the adapter ignores it and audits.
4. **Validate approval-bundle integration.** A bundle that includes
   MCP tool calls should still work end-to-end.

Without these checks, spec-007 is just "wire the pipes." With them,
spec-007 validates the design positions before we commit to building
out the P1-P2 work.

---

## 6. Open follow-ups (not blocking)

1. **`cd:` annotation governance.** Should the namespace be claimed
   as an MCP spec extension via the experimental capability mechanism?
   That would let other clients honor it too.
2. **Community policy module registry.** Are policy modules per
   §3.2 going to ship with CapableDeputy, or be community-curated?
   Both have trade-offs.
3. **Operator-ratified ritual integration.** When a prompt is
   marked `cd:operator_ratifiable`, how does the ritual save flow
   look? (depends on spec-005 V024-V029)
4. **The `flow-pattern-session` definition.** §3.7 uses
   (purpose_handle, axis_d.initiator) as the session identity. Is
   that the right grain, or should the session_id itself be the
   binding? Trade-off: stronger isolation vs. usable UX.
