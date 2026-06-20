# MCP Compatibility and Security Review

Last refreshed: 2026-06-20.

This document is the implementation yardstick for CapDep's MCP support. MCP is
an integration substrate, not a security boundary. Every MCP-originated action
must become a normal daemon-mediated CapDep action before it can touch data,
credentials, apps, files, email, browser, or network.

## Current MCP Support

| Surface | Daemon-as-server status | Upstream-adapter status | Security posture |
| --- | --- | --- | --- |
| `tools/list` | Implemented with `inputSchema`, `outputSchema`, annotations, and CapDep `_meta`. | Implemented by wrapping upstream tools as CapDep `ToolDefinition`s. | Tool metadata is advisory; daemon capability checks remain authoritative. |
| `tools/call` | Implemented through daemon `tool.call`. | Implemented through policy-gated wrapper handlers. | Always capability checked, label checked, audited, and provenance-linked by the daemon. |
| `resources/list` | Implemented for labeled CapDep memory resources. | Implemented for upstream resources when the server supports it. | Metadata may expose labels, but list results do not grant read authority. |
| `resources/read` | Implemented through `memory.read`, so policy and label propagation match the tool path. | Implemented with server and content `_meta` label propagation. | Treat every upstream resource read as labeled input; untrusted sources must carry untrusted/external provenance. |
| `prompts/list` / `prompts/get` | Implemented for canonical workflow prompts. | Not auto-forwarded from upstream servers. | Prompts are untrusted instructions. They can describe workflows but never grant capability. |
| Elicitation | Implemented only when daemon `tool.call` returns a queued `approval_id`. | Mediator ports exist; upstream server-initiated elicitation remains refused or explicitly mediated by policy. | Elicitation can approve an existing daemon approval object; it cannot create hidden authority. |
| Logging notifications | Implemented for session-bound MCP policy status. | Not broadly proxied. | Advisory only; never grants authority or triggers action. |
| Tool list changes | Implemented for session capability grants. | Upstream cache invalidation should be conservative until notification proxying is complete. | Advisory only; clients must rediscover and still pass daemon checks. |
| Sampling | Not exposed to MCP clients. | Mediator ports exist; default posture is refuse unless explicitly enabled per server/session. | Refuse by default. If enabled, expose no tools to the sampled model unless separately approved. |
| Roots | Not exposed. | Not consumed by default. | Trusted servers only because roots can reveal local workspace structure. |
| Resource subscriptions | Not implemented. | Not proxied by default. | Treat as advisory and never auto-fetch without an operator/session request. |
| Daemon control client | Implemented as `capdep mcp-control-server`. | Not applicable; this is a CapDep client surface, not an upstream server loaded by CapDep. | Forwards named operations to daemon RPCs. The daemon remains responsible for policy, approval, provenance, and audit enforcement. |

## Daemon Security Contract

1. `tools/call` is the chokepoint. MCP servers and hosts only request actions; `LabeledToolClient` decides, dispatches, records use, writes audit events, updates labels, and records provenance.
2. Upstream MCP tools must be mapped to capability kinds. Strict mode refuses unclassified tools rather than silently granting a read fallback.
3. Upstream server labels are a floor, not a ceiling. Tool `_meta`, resource `_meta`, result inspectors, and source label lookups can only raise labels.
4. Approval happens through daemon approval objects. MCP elicitation can accept or decline an existing queued request, but it cannot submit a new request or mutate session capabilities.
5. Admin MCP is separate from session MCP. Admin tools are local setup authority and carry `io.capabledeputy/surface=admin`; normal session-bound MCP cannot access setup operations.
6. OAuth tokens and client secrets remain daemon-owned. MCP tools may initiate setup flows, but secrets are stored through daemon RPCs and never exposed to the planner session as tool results.
7. Control MCP is a daemon client like the CLI, TUI, or GUI. It may expose approval and tool-call controls to a trusted MCP host, but those operations must be daemon RPCs and must not duplicate or bypass daemon safety logic.
8. Unsupported MCP surfaces must fail closed or remain absent. Missing support should be visible in this matrix rather than implemented as permissive passthrough.

## ARD Scope

Agentic Resource Discovery (ARD) is a discovery/catalog specification, not a
tool execution protocol. CapDep should implement ARD only as an operator-facing
discovery assistant:

- ARD can discover candidate MCP servers, tools, APIs, skills, or agents.
- Discovered resources should become config drafts or review items.
- The operator must explicitly curate and enable a discovered server before use.
- Once enabled, execution still flows through the upstream MCP adapter, capability mapping, labels, policy, approval, provenance, and audit.

ARD must not allow a model to discover and call arbitrary online tools at
runtime. That would bypass CapDep's intended anti-confused-deputy design.
