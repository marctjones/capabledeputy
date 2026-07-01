# Desktop Agent UX and Daemon RPC Plan

Issue: #147  
Milestone: v0.35.0

## What peer products imply

- OpenAI ChatGPT on macOS surfaces app context only with explicit user action
  and a visible app-context banner. Its public help page says Codex/ChatGPT
  can read supported app context, including recent terminal lines, through
  macOS Accessibility APIs, and can stage IDE diffs for user review:
  https://help.openai.com/en/articles/10119604-work-with-apps-on-macos
- Claude Code spans terminal, IDE, desktop, and browser surfaces. Its desktop
  app emphasizes visual diff review, multiple sessions, scheduled/background
  work, and MCP-backed extension points:
  https://code.claude.com/docs/en/overview
- Claude Code's MCP documentation treats HTTP MCP OAuth as a first-class
  connection path, stores credentials outside prompts, supports scoped OAuth,
  and makes browser login a deliberate user action:
  https://code.claude.com/docs/en/mcp
- Goose extensions are MCP-based add-ons with explicit extension discovery,
  enable/disable controls, and permission controls; autonomous behavior is
  paired with access-control modes and ignored paths:
  https://goose-docs.ai/docs/getting-started/using-extensions/

## CapDep UX rules

1. Context capture is visible. Clients show the active app/source, captured
   identity, staleness, and labels before the daemon uses it.
2. SourcePorts are the unit of trust. Browser pages, Apple Mail messages,
   Finder files, iWork documents, and Calendar events enter through canonical
   SourcePort IDs rather than raw client strings.
3. Review cards summarize exact artifacts. A user approves a hash-bound draft,
   diff, calendar mutation, document patch, or research memo, not a vague
   natural-language intent.
4. Approval fatigue is reduced by grouping review context, artifact effect,
   destination, labels, and hash into one card. Repeated low-risk reads should
   use grants; writes and sends should stay explicit.
5. The daemon owns policy. Swift, TUI, CLI, and MCP-control can render cards
   and request launches, but daemon RPCs own SourcePorts, artifacts,
   approvals, OAuth, tool dispatch, labels, and audit events.

## RPC surface

The v0.35 desktop path is:

1. Client captures local foreground hints.
2. Client sends them to `source_context.import`.
3. Daemon returns canonical SourcePorts through `source_context.canonicalize`.
4. Agent proposes a typed artifact.
5. Client or daemon calls `artifact.prepare`.
6. Daemon returns `review_artifact` metadata plus the exact payload bytes.
7. Review UI fetches `approval.detail` and renders the same `review_artifact`.
8. User approves or rejects through existing approval RPCs.

Required client affordances:

- Context chip: source kind, stable ID, captured time, stale status, and labels.
- Review card: artifact title, effect, destination, type, hash, labels, and
  preview.
- Recovery state: stale context, ambiguous Mail subject, missing file URI, or
  unsupported app should produce a daemon error with a clear next action.

## Anti-patterns

- Reading arbitrary app text into prompts without a SourcePort.
- Treating a visible app name as sufficient provenance.
- Letting clients mint approval payloads or artifact hashes.
- Showing approval text without the destination, effect, and hash.
- Hiding OAuth or MCP-extension admission behind first use.

## v0.35 implementation evidence

- Browser and macOS active context now canonicalize through SourcePorts.
- Apple Mail, Finder, Pages, Numbers, Keynote, and Calendar have app-specific
  SourcePort canonicalization paths with fail-closed ambiguous-input handling.
- Typed artifacts expose `review_artifact` cards in daemon responses.
- CapDepMac parses and renders visual review cards from approval details.
