# MCP Extension Admission Research

Issue: #154  
Milestone: v0.36.0

## Peer signals

- Claude Code exposes MCP servers as project/user/local configuration,
  supports HTTP OAuth login, stores credentials outside prompt context, and
  lets operators constrain OAuth scopes:
  https://code.claude.com/docs/en/mcp
- Claude Code also documents a broader extension model with skills, hooks,
  custom commands, subagents, background agents, and desktop/browser review
  flows:
  https://code.claude.com/docs/en/overview
- Goose treats extensions as MCP-based add-ons and pairs extension management
  with permission modes, tool-level permissions, and ignored paths:
  https://goose-docs.ai/docs/getting-started/using-extensions/
- The OpenHands platform and SDK papers describe agentic software-development
  systems with sandboxed execution, lifecycle control, and visual workspaces:
  https://arxiv.org/abs/2407.16741 and https://arxiv.org/abs/2511.03690

## Admission model

CapDep should treat every MCP server or workflow template as an admission
object, not a convenience setting. Admission has five steps:

1. Parse: load the server/template manifest and reject malformed input.
2. Classify: map each exposed tool to an explicit `CapabilityKind`, target
   pattern, labels, and effect class.
3. Preview: persist a daemon-owned admission record with warnings, refused
   tools, and a fingerprint of the reviewed mapping.
4. Approve: enable only the reviewed fingerprint. A changed mapping returns to
   `needs_reapproval`.
5. Disable: preserve audit history while removing the extension from usable
   tool dispatch.

## Trust tiers

- Official remote or reference servers: allow curated configs, but still
  require strict mapping and OAuth/session status.
- Tier-1 mapping fixtures: GitHub, Google Workspace, Microsoft 365, and
  Notion stay visible in the catalog; unverified endpoint details remain
  operator-reviewed before loading.
- Community servers: require explicit isolation, host/network allowlists,
  version pinning, and a fresh preview after package upgrades.
- Shared templates: do not grant authority by themselves. A template can
  declare capabilities and source ports, but session grants and approvals are
  still daemon decisions.

## Client behavior

Clients may:

- request `mcp.admission.preview`, `approve`, `disable`, `list`, and `audit`;
- render warnings, rejected tools, target mappings, and fingerprints;
- launch already-admitted workflow templates with `workflow.launch`.

Clients may not:

- register tools directly;
- store OAuth access tokens;
- bypass strict mapping by invoking unknown tools;
- treat a template author as a policy authority.

## v0.36 implementation evidence

- `McpAdmissionStore` persists preview, approval, disable, status, fingerprint,
  and audit events in the daemon state database.
- Daemon RPCs expose `mcp.admission.preview`, `approve`, `disable`, `list`,
  and `audit`; MCP-control can call those operator surfaces.
- `workflow.launch` centralizes template launch across CLI, TUI, Swift, and
  MCP-control.
- OAuth helpers expose redacted credential status and enforce requested scopes
  before returning a bearer token.
- Curated configs include strict tier-1 mapping fixtures for GitHub, Google
  Workspace, Microsoft 365, and Notion.
