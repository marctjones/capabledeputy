# MCP Protocol Fit — Security-Model Audit

**Audit date:** 2026-05-20
**Spec audited:** [MCP 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25)
**Auditing:** `src/capabledeputy/upstream/` (the existing partial adapter)
**Purpose:** Determine, surface-by-surface, what we currently do, what the
spec requires, and how CapableDeputy can support each MCP protocol surface
**without breaking the policy chokepoint, label propagation, or override
discipline**.

This document is the architectural prerequisite for spec-007 (stub-as-MCP
integration tests) and spec-008 (daemon-as-MCP-server). Both depend on
the design positions taken here.

---

## TL;DR

The current adapter implements **2 of 8 MCP protocol surfaces** (tools/list,
tools/call) and ignores the rest. That's actually a defensible default
posture, but it leaves us with no story for the surfaces that *real*
production MCP servers use heavily. Particularly:

- **Sampling** (a server directing our LLM) is the highest-risk surface
  and the one we MUST refuse by default.
- **Elicitation** (a server prompting our user) is the second-highest
  risk and likewise defaults to refused.
- **Resources** (server-provided data) MUST be wired in but with a hard
  default of `UNTRUSTED_EXTERNAL` and **no subscription support**.
- **Prompts** can be supported but only as user-initiated content
  with the same untrusted label.
- **Roots** is metadata leak; declare only for trusted servers.
- **Notifications** that push data out-of-band MUST be ignored or
  re-routed through the chokepoint.

Capability declaration becomes a **fail-closed authority strategy**:
we declare a tiny set of capabilities by default, and operators opt
specific servers in.

---

## Current state (audit)

| Surface | Implemented? | Notes |
|---|---|---|
| Initialize / capability negotiation | ✓ | Basic handshake via MCP SDK; no custom capability declaration |
| `tools/list` | ✓ | Heuristic `capability_kind` inference + per-tool config override + fail-closed (strict=True) for unmapped tools |
| `tools/call` | ✓ | Routes through `LabeledToolClient.call_tool()` → `engine.decide()` |
| `resources/list` | ✗ | Not implemented |
| `resources/read` | ✗ | Not implemented |
| `resources/subscribe` | ✗ | Not implemented (and shouldn't be — see below) |
| `prompts/list` | ✗ | Not implemented |
| `prompts/get` | ✗ | Not implemented |
| `sampling/createMessage` | ✗ | Not implemented |
| `elicitation/create` | ✗ | Not implemented |
| `roots/list` | ✗ | Not implemented (and currently not declared as a client capability either) |
| `notifications/*` (tool/resource/prompt list_changed) | ✗ | Not handled |
| `notifications/resources/updated` | ✗ | Not handled |
| Authorization (OAuth 2.1 for remote servers) | ✗ | No HTTP transport exists yet |
| Streamable HTTP transport | ✗ | stdio only |
| Subprocess isolation | ✓ | `isolation.py` runs upstream servers in podman/docker with restricted network, dropped capabilities, memory/CPU limits |
| Tool annotations treated as untrusted | ✓ | Heuristic + operator override; strict mode is default |

---

## Surface-by-surface design

### 1. Tools (`tools/list`, `tools/call`)

#### What the spec says

- Servers expose tools with `name`, `inputSchema`, optional
  `outputSchema`, optional `annotations` (`readOnlyHint`, `destructiveHint`,
  `idempotentHint`, `openWorldHint`).
- **The spec explicitly warns**: "clients MUST consider tool annotations
  to be untrusted unless they come from trusted servers."
- Tool descriptions and annotations are written by the server; they are
  hints, not commitments.

#### Current CapableDeputy posture

✓ **Aligned.** The adapter uses heuristic inference + per-tool operator
overrides + fail-closed (`strict=True`) for unmapped tools. Annotations
are read but not blindly trusted.

#### Gaps

- The heuristic uses the tool *name* as a signal — a malicious server
  could name a destructive tool `read_helper` to get classified as
  `READ_FS`.
- The operator override pattern is correct but requires the operator
  to enumerate every tool from every server.
- There's no explicit "this server is trusted" flag that lets annotations
  be honored.

#### Recommendation

1. Keep the strict=True default (current behavior).
2. Add a `trust_tier` config per upstream server: `unvetted` (default),
   `operator-curated`, or `vendor-vetted`. Only `vendor-vetted` lets
   annotations override the heuristic; the others require operator
   mapping.
3. Treat the tool description and annotations as **never** authoritative
   for `effect_class`, `reversibility`, or `social_commitment` — these
   are operator-curated only.
4. Log a `MCP_HEURISTIC_GUESS` audit event whenever the heuristic picks
   a capability kind that contradicts what the server's annotations
   claim, so operators can see drift.

#### Security claim preserved

The policy chokepoint runs on every `tools/call`. Whatever the server
claims about its tool, our cap match + Brewer-Nash + reversibility gate
all apply.

---

### 2. Sampling (`sampling/createMessage`)

**Risk: CRITICAL.** This is the surface where a server can direct the
client's LLM. The server controls the prompt; the LLM produces output
the server receives. Modern variants include nested tool-use loops that
make this even worse: a server can call into our LLM, get tool-use
responses, execute those tools, and continue the conversation
recursively.

#### What the spec says

- Clients declaring `sampling` capability MUST review every
  `sampling/createMessage` with a human in the loop (spec language).
- Users MUST control whether sampling happens, the prompt sent, and
  the response returned.
- Tool-use variant: client declares `sampling.tools`, server can ask
  the LLM to call tools; each tool-use round-trip needs approval.

#### Current CapableDeputy posture

✗ Not implemented. We don't declare the `sampling` capability.

#### Recommendation: **DO NOT declare `sampling` capability**

This is the correct security-model fit. Rationale:

1. **The server is untrusted.** Letting it set a prompt is letting it
   set the planner LLM's goals. That's the entire risk CapableDeputy
   was built to mediate.
2. **Tool-use sampling makes it worse.** A server could ask our LLM to
   call our tools (which our LLM has standing capabilities for) using
   server-supplied prompts. The capability/Brewer-Nash gates would still
   fire, but the *intent* of the actions would come from the server's
   prompt — which is exactly the prompt-injection vector our design
   refuses to participate in.
3. **There's no operator-reviewable path for non-trivial sampling.**
   The spec says "human in the loop" but doesn't define what review
   means for a 50-message multi-turn sampling tree.

If an operator has a legitimate need (e.g., a specialized server that
needs to summarize before returning), the right path is:
- Wrap the server's intended use in an operator-authored tool
- Make that tool call our quarantined extractor on the server-supplied
  text
- Return the schema-bounded result

That is Pattern ② DUAL_LLM with the operator's hand on the schema.

**Action:** keep the `sampling` capability undeclared at initialization.
If a server attempts `sampling/createMessage` anyway, return JSON-RPC
error `-1` (user rejected) immediately. Audit the rejection.

---

### 3. Elicitation (`elicitation/create`)

**Risk: HIGH.** A server asks the user for input out-of-band. The user's
answer is returned to the server.

#### What the spec says

- Two modes: `form` (server defines a JSON-schema-bounded form; server
  receives the user's answer) and `url` (server gives user a URL to
  open; user interacts on a third-party page; URL flow is separate from
  the MCP client).
- Form mode MUST NOT be used for passwords, API keys, payment data.
- URL mode is for sensitive data and OAuth flows.
- Strict client-side rules around URL handling (no auto-prefetch, no
  auto-open, full URL displayed to user, secure browser, etc.).

#### Current CapableDeputy posture

✗ Not implemented. We don't declare the `elicitation` capability.

#### Recommendation: declare nothing initially; opt-in per-server later

Initial position:
- **Do not declare `elicitation` capability** at initialization. Servers
  attempting `elicitation/create` get JSON-RPC `-32601` (method not
  found via missing capability) or `-32602` (invalid params).
- **Audit every refused elicitation** so operators see what servers are
  trying to ask for.

Later (post-spec-007), opt-in per server:
- For `vendor-vetted` servers, optionally declare `elicitation.form` —
  but route every elicitation request through the operator's TUI
  (NOT the LLM context). The form schema is bounded enough we can
  render it as a structured form.
- For OAuth flows (URL mode), declare `elicitation.url` with strict
  per-server config. The URL is shown to the operator with the domain
  highlighted; no auto-open.

**Key security promise:** the user/operator's answer to an elicitation
MUST be treated as `EXTERNAL_UNTRUSTED` if it flows back to the
server's context. If a server is asking the user for, say, a search
query, that query becomes server-context data. We don't lose anything
by being conservative here.

---

### 4. Resources (`resources/list`, `resources/read`, `resources/subscribe`)

**Risk: MEDIUM.** Servers expose data with URIs; clients read it. This
maps cleanly to CapableDeputy's existing read-with-label-propagation
pattern (`web.fetch`, `inbox.read`, `fs.read`).

#### What the spec says

- `resources/list` enumerates available resources with URI, MIME type,
  name, optional description.
- `resources/read` returns content (text or base64 blob).
- `resources/subscribe` — server sends `notifications/resources/updated`
  when the resource changes.
- Resource annotations include `audience`, `priority`, `lastModified` —
  HINTS, not authoritative.

#### Recommendation

1. **Implement `resources/list` and `resources/read`** as adapter-mapped
   tools, similar to how `inbox.list`/`inbox.read` work today.
2. **Default label: `UNTRUSTED_EXTERNAL`** on every read result. Don't
   let server-supplied annotations downgrade the label.
3. **The URI is the canonical destination id** (FR-043/FR-048). Audit
   it on every read.
4. **DO NOT declare `resources.subscribe` capability.** Subscription
   pushes updates out-of-band of `decide()`. If an operator wants
   polling, build a tool that does `resources/read` on a timer with
   operator-declared intent.
5. **DO declare `resources.listChanged`** — but on receipt, mark the
   tool list stale; re-discover on next operator session, not
   automatically.

For tool results that contain `resource_link` or embedded `resource`
content blocks: when the adapter unpacks the result, treat the embedded
content as UNTRUSTED_EXTERNAL just like `web.fetch` content.

---

### 5. Prompts (`prompts/list`, `prompts/get`)

**Risk: MEDIUM.** Server provides templated messages; client invokes
them (typically as user-initiated slash commands).

#### What the spec says

- `prompts/list` enumerates prompts with name, optional arguments.
- `prompts/get` returns the templated messages (with arguments
  substituted server-side).
- Messages can include text, images, audio, or embedded resources.
- "User-controlled" per the spec — typically a slash command.

#### Recommendation

1. **Implement `prompts/list` and `prompts/get`** as `READ_FS`-capability
   operations.
2. The returned messages are **server-supplied content**. Treat them
   as `UNTRUSTED_EXTERNAL` when they flow onto the session.
3. **DO NOT auto-execute prompts.** When the operator invokes a
   `/prompt_name` slash command, the messages are displayed in the
   operator's TUI; the operator decides whether to forward them to
   the LLM.
4. Treat embedded resources in prompt messages the same as embedded
   resources in tool results (UNTRUSTED_EXTERNAL).

This is the operator-explicit pattern: the LLM never gets server-
authored prompt content without the operator's hand on it.

---

### 6. Roots (`roots/list`)

**Risk: LOW (metadata leak).** Client tells server which filesystem
directories the server can operate in.

#### What the spec says

- Client declares `roots` capability.
- Server can request `roots/list` to learn the workspace boundaries.
- Used by, e.g., a filesystem MCP server to scope its operations.

#### Recommendation

1. **By default, DO NOT declare `roots`.** That keeps untrusted servers
   from learning our directory structure.
2. **For `vendor-vetted` servers, optionally declare `roots`** with a
   tightly scoped list — only the operator-configured bindings (from
   `PolicyContext.bindings`) for that server's category.
3. Each root the server learns about is a piece of operational metadata.
   Treat the per-server roots list as an operator-curated capability
   grant — declared in config, not derived from the OS.

---

### 7. Notifications

| Notification | Risk | Handling |
|---|---|---|
| `notifications/tools/list_changed` | low | Mark tool list stale; re-discover on operator's next interaction |
| `notifications/resources/list_changed` | low | Same |
| `notifications/prompts/list_changed` | low | Same |
| `notifications/resources/updated` | **HIGH** | **Refuse / ignore.** This pushes content out-of-band of `decide()`. If operator wants updates, they poll. |
| `notifications/elicitation/complete` | medium | Only relevant if we declare `elicitation`. Audit on receipt; do not auto-action. |
| `notifications/progress` | low | Audit; surface to TUI |
| `notifications/cancelled` | low | Propagate cancellation to the in-flight tool call |
| `notifications/message` (logging) | low | Audit; surface to TUI |

---

### 8. Authorization (OAuth 2.1 for remote servers)

**Status: not applicable until HTTP transport lands.**

#### Recommendation when we add HTTP

1. OAuth flow runs entirely in the daemon. Bearer tokens never enter
   any LLM context.
2. Tokens stored via `src/capabledeputy/secrets.py`.
3. The MCP server is the OAuth resource server; CapableDeputy is the
   OAuth client.
4. URL elicitation (if declared) is a separate concern — it's for
   the server's OAuth client role to *other* services, not our auth
   to the server.

---

### 9. Transports

- **stdio**: current, working. Subprocess + podman/docker isolation.
- **Streamable HTTP**: NOT YET. Future work. When added: Origin
  validation MUST happen, localhost binding MUST be the default,
  OAuth MUST be wired in before remote (non-localhost) servers are
  allowed.

---

### 10. Capability declaration strategy (what we tell servers we support)

This is the **fail-closed authority** policy. At MCP initialize, we declare:

```json
{
  "capabilities": {
    "roots": {}                  // declared without listChanged; opted-in per-server
  }
}
```

Notably **NOT declared by default:**
- `sampling` — never (security position)
- `elicitation` — opt-in per server, post-spec-007
- `roots.listChanged` — opt-in per server

Servers calling capabilities we didn't declare get a JSON-RPC error.
Every refused capability call is audited.

---

## Implementation roadmap

| Priority | Item | Spec | Effort |
|---|---|---|---|
| P0 | `resources/list` + `resources/read` (with UNTRUSTED_EXTERNAL default) | section 4 | 2 days |
| P0 | Refuse `sampling` and `elicitation` requests with audit | sections 2, 3 | 0.5 days |
| P0 | Per-server `trust_tier` config (`unvetted` / `operator-curated` / `vendor-vetted`) | section 1 | 1 day |
| P0 | `MCP_HEURISTIC_GUESS` audit event when heuristic disagrees with annotations | section 1 | 0.5 days |
| P1 | `prompts/list` + `prompts/get` with operator-explicit invocation | section 5 | 2 days |
| P1 | `notifications/resources/updated` refusal | section 7 | 0.5 days |
| P1 | `notifications/tools/list_changed` → mark-stale handler | section 7 | 0.5 days |
| P2 | Per-server scoped `roots` declaration | section 6 | 1 day |
| P2 | Embedded-resource label propagation in tool results | section 4 | 1 day |
| P3 | Streamable HTTP transport + OAuth 2.1 | section 8-9 | 2 weeks |

**Sum P0-P2:** ~9 days of focused work. **P3 is its own spec.**

---

## Open design decisions

These need operator/principal review before spec-007 starts:

1. **`trust_tier` defaults.** Anthropic's reference MCP servers — should
   they be `operator-curated` by default, or still `unvetted`? My
   recommendation: `unvetted` for everything; the operator pins specific
   servers as needed.

2. **Prompt messages on the operator's TUI.** Do we surface them as
   "click to forward to LLM," or do we render them as read-only
   reference (operator manually composes)? Trade-off: more friction =
   safer; less friction = usable.

3. **Heuristic-disagrees-with-annotation policy.** Today we silently
   prefer the heuristic. Should that *refuse to register* the tool
   when the disagreement is severe (e.g., heuristic says `READ_FS` but
   `destructiveHint=true`)?

4. **OAuth flow location.** When we add HTTP transport, do tokens live
   in the existing per-uid secrets store, or do we need per-server
   isolation (e.g., systemd-credentials)? Recommendation: existing
   secrets store with per-server keying — but document the threat model.

5. **Should the daemon expose a CapableDeputy-as-MCP-server endpoint
   (spec-008)?** That's a separate question, but the protocol-fit
   work here is a prerequisite — the design must hold from both
   directions.

---

## What this audit changes about spec-004

The original `specs/004-mcp-and-substrate/tasks.md` (U001-U060) covered
adapter wiring but did not engage the security model question
surface-by-surface. Net additions from this audit:

- New `U005A`: per-server `trust_tier` config
- New `U015A`: refuse-sampling-with-audit handler
- New `U015B`: refuse-elicitation-with-audit handler
- New `U016A`: resources/list + resources/read adapter
- New `U016B`: prompts/list + prompts/get adapter
- New `U017A`: notification refusal/handling logic
- Renamed: HTTP-transport tasks deferred to spec-009 (was implied
  within 004)

These should be added to `tasks.md` when the audit conclusions are
ratified.
