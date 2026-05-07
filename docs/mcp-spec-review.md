# MCP Spec Coverage Review

Review of CapableDeputy's MCP server (`src/capabledeputy/mcp_server/server.py`)
against the MCP specification at
[modelcontextprotocol.io/specification/2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25).

The spec defines a JSON-RPC 2.0 protocol with three top-level
primitive sets (server features, client features, utilities) plus
authorization. Below is what we use, what we don't, and why.

## Server features

### Tools — fully used

| Spec feature | Status | Notes |
|---|---|---|
| `tools/list` | ✓ used | Discovers tools from the daemon's `tool.list` RPC |
| `tools/call` | ✓ used | Dispatches via daemon's `tool.call` RPC, returns CallToolResult |
| `name` (1-128 chars, alphanumeric + `_-.`) | ✓ used | Our names like `memory.read` are spec-valid |
| `title` | ✓ used | Set to the same as `name` for now; could be more human-friendly |
| `description` | ✓ used | Pulled from each tool's `ToolDefinition.description` |
| `inputSchema` (full JSON Schema) | ✓ **fixed in 7f** | Was hardcoded `{"type": "object"}` — bug; now uses real `parameters_schema` |
| `outputSchema` | ✗ unused | We could derive from tool output dict shape; deferred |
| `icons` | ✗ unused | Cosmetic; defer |
| `annotations` (readOnlyHint, destructiveHint, idempotentHint, openWorldHint) | ✓ used | Mapped from `capability_kind` via `_ANNOTATIONS_BY_KIND`. Lets hosts decide when to prompt for confirmation per spec UI guidance |
| `_meta` (under reserved namespace) | ✓ used | We expose `io.capabledeputy/capability_kind` and `inherent_labels` so capability-aware hosts can filter |
| `execution.taskSupport` | ✗ unused | Task-augmented execution is for long-running tools; not relevant for our local in-process dispatch |
| `tools/list_changed` notification | ⚠ partial | Server supports it via SDK but we never emit it. If a session's capability set changes at runtime, we could notify |

### Tool result content types — partially used

| Content type | Status |
|---|---|
| `text` | ✓ used as primary |
| `image` | ✗ not applicable to our tools |
| `audio` | ✗ not applicable |
| `resource_link` | ✗ unused (could link to memory entries) |
| Embedded `resource` | ✗ unused |
| `structuredContent` | ✓ used when output is a dict |
| `isError` flag | ✓ **used in 7f** for denials and errors per spec §"Tool Execution Errors" |

### Resources — unused

| Spec feature | Status | Notes |
|---|---|---|
| `resources/list` | ✗ | We don't expose anything as resources |
| `resources/read` | ✗ | |
| `resources/templates/list` | ✗ | |
| `resources/subscribe` + `notifications/resources/updated` | ✗ | |
| `resources/list_changed` | ✗ | |

**Why:** Our memory store *could* be exposed as resources (e.g., URI
`capdep:memory/{key}` returning the labeled value). This would let
MCP hosts that don't speak our tools but understand resources still
read memory directly. Deferred because:
- Tools already cover the read use case for our agent flow.
- Resources don't pass through `LabeledToolClient` in the same way,
  so they'd need separate label-propagation plumbing.
- Spec resource model is closer to "passive content" than "policy-gated
  reads" — using it would muddy our security boundary.

**Verdict:** Skip for v0.1. Reconsider if we add a host-side wrapper
that consumes upstream MCP servers (some servers ship important
content via resources, e.g. filesystem MCP).

### Prompts — unused

| Spec feature | Status |
|---|---|
| `prompts/list` | ✗ |
| `prompts/get` | ✗ |
| `prompts/list_changed` | ✗ |

**Why:** Prompts are reusable templates that hosts can present to
users. CapableDeputy doesn't have user-facing prompt templates yet.
Could expose canonical workflows here in the future ("review my
prescription," "summarize this email," etc.) so MCP hosts can offer
them as one-click options.

**Verdict:** Defer; useful for v0.2 ergonomics.

## Client features (server-initiated requests)

These are things our *server* could ask the *client* (host) to do.

| Feature | Status | Notes |
|---|---|---|
| Sampling (`sampling/createMessage`) | ✗ | We have our own LLM client. No need to ask the host to do completions for us |
| Roots (`roots/list`) | ✗ | We don't access the client's filesystem; our memory is in-process |
| Elicitation (`elicitation/create`) | ✗ but **interesting** | Could surface approval requests directly to the host's user via elicit, instead of "go run capdep approval approve" out-of-band |

**Elicitation deserves attention:** when our daemon receives a tool
call that requires approval, we could invoke `elicitation/create` on
the MCP host to get a yes/no decision in-flow. That's a more natural
UX than the current model where the user has to switch to a separate
terminal to run `capdep approval approve N`.

**Verdict:** Implementing elicitation for approvals is the highest-value
unused feature. Worth a focused phase.

## Utilities

### Notifications and tracking

| Feature | Status | Notes |
|---|---|---|
| Cancellation (`notifications/cancelled`) | ✗ | Our tool calls are synchronous in the SDK; no cancellation propagation |
| Progress (`notifications/progress`) | ✗ | None of our tools are long-running enough to warrant progress |
| Logging (`notifications/message`) | ⚠ should add | We could emit policy decisions, label propagations, and tool dispatches as structured logs to the host |
| Pagination (cursor in list responses) | ✗ | Our tool list is small (~10 tools); no need yet |
| Ping (`ping`) | ✓ via SDK | SDK handles automatically |
| Completion (`completion/complete`) | ✗ | Argument autocompletion for tools/prompts; nice-to-have |

**Logging deserves a fix:** the spec defines structured log levels
(debug/info/notice/warning/error/critical/alert/emergency). We have
a rich audit log internally. Mirroring policy-relevant events to the
MCP host as `notifications/message` would let host UIs surface them
in real time without polling the audit log.

### Lifecycle

| Feature | Status |
|---|---|
| `initialize` request with capability negotiation | ✓ via SDK |
| `protocolVersion` matching | ✓ via SDK |
| `notifications/initialized` | ✓ via SDK |
| Graceful shutdown | ✓ via stdio EOF |

The Python SDK handles lifecycle cleanly. We don't need custom code here.

### Authorization

| Feature | Status | Notes |
|---|---|---|
| OAuth 2.1 resource server (`TokenVerifier`) | ✗ | We're stdio-only and trust-on-the-other-side-of-the-pipe |
| OpenID Connect Discovery | ✗ | Same — irrelevant for local stdio |

The 2025-11-25 spec added OIDC discovery and scope negotiation.
Useful only when the MCP server is exposed over HTTP/SSE. Our stdio
server runs as a subprocess of the host; trust is established by
spawning, not by token validation.

**Verdict:** Skip until we ship an HTTP/SSE transport.

## Transports

| Transport | Status |
|---|---|
| stdio | ✓ used |
| SSE (Server-Sent Events) | ✗ |
| Streamable HTTP | ✗ |

**Why stdio:** The MCP host (Claude Code, etc.) launches our server
as a subprocess and trust is established by the spawn. No network,
no auth, no exposure. This is the correct deployment model for a
security wrapper.

We could add HTTP/SSE for **service deployments** where multiple
hosts share one CapableDeputy daemon (a household setup, an
organizational installation). Different threat model — would
require auth, TLS, and the OAuth/OIDC features we're skipping
today. Phase v0.3.

## Spec features we deliberately do not use, and why

1. **Resources for memory contents.** Tools already cover the
   policy-gated read use case. Adding resources would require a
   second label-propagation path.
2. **Sampling.** We don't need the host to do LLM completions for us
   — we have our own LLM client.
3. **Roots.** We don't access the client's filesystem.
4. **OAuth/OIDC.** Stdio-only deployment doesn't need it.
5. **Pagination.** Our tool count is small.
6. **Image/audio/resource_link content.** Our tools don't produce
   them.

## Spec features we should consider next

In priority order:

1. **Elicitation for approval flow.** When a tool call would queue
   an approval, surface the approval modal directly through the MCP
   host's user interface via `elicitation/create`. Eliminates the
   "switch to a separate terminal" step.
2. **Logging notifications.** Mirror our audit log's policy decisions
   and label propagations to the host as structured `notifications/
   message` events. Turns the host's chat UI into a live audit
   viewer for free.
3. **`tools/list_changed` notifications.** When a session's
   capability set changes (capability granted/revoked), the visible
   tool set changes. Currently the host won't see the change until
   it polls; a notification would push it.
4. **Resource exposure of memory.** Worth doing if/when we want to
   support hosts that don't speak our tools but do speak MCP
   resources.
5. **Output schemas for tools that return structured data.**
   Memory.read returns `{found, value}` — could be schemafied.
   Helps clients validate and helps the LLM understand the shape
   without trial and error.

## What changed in 7f

- Bug: `inputSchema` was hardcoded to `{"type": "object"}` for every
  tool. Now uses the real `parameters_schema` via the daemon's
  `tool.list` response.
- Now sets `isError=true` on policy denials and tool errors.
- Now sets `structuredContent` for dict outputs.
- Now sets `annotations` (readOnlyHint / destructiveHint /
  openWorldHint / idempotentHint) per capability kind.
- Now sets `_meta` with `io.capabledeputy/capability_kind`,
  `inherent_labels`, `decision`, `rule`, `effective_labels`,
  `labels_added`.
- Tests updated to verify the spec-shaped fields.

## References

- [MCP specification 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25)
- [Tools spec](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
- [Resources spec](https://modelcontextprotocol.io/specification/2025-11-25/server/resources)
- [Prompts spec](https://modelcontextprotocol.io/specification/2025-11-25/server/prompts)
- [Elicitation spec](https://modelcontextprotocol.io/specification/2025-11-25/client/elicitation)
- [Python SDK](https://github.com/modelcontextprotocol/python-sdk)
