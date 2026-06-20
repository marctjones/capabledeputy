# Architecture

CapableDeputy is organized around one invariant: every tool call crosses a
runtime-owned policy chokepoint before any side effect happens. The LLM proposes
actions; CapDep classifies the tool, target, labels, flow pattern, and
capability, then decides whether to allow, deny, require approval, or require an
override.

## Current Runtime Seams

- `ToolDefinition` is the compatibility runtime object. It still carries the
  handler, but it now exposes split descriptors for runtime shape, policy
  classification, and information-flow behavior.
- `DecisionRequest` and `PolicyPipeline` are the policy decision boundary. The
  default pipeline delegates to the existing engine, but dispatcher code no
  longer needs to pass a long argument list directly into the monolithic engine.
- `RuntimeManifest` is the normalized, side-effect-free view of configured
  tools, upstream MCP servers, and policy hooks. Daemon startup reports a compact
  manifest summary and fails on manifest errors.
- `HookRegistry` is the canonical extension surface. Legacy tuple fields on
  `PolicyContext` still work, but hook execution consumes named lifecycle hooks.

## Configuration Direction

Configuration should compile in this order:

1. Load curated presets, personal-assistant overrides, `servers.d`, Starlark
   scripts, and local policy files.
2. Normalize them into descriptors and a `RuntimeManifest`.
3. Validate every tool has a capability kind, effect operation, policy target,
   risk citation, and flow metadata.
4. Start runtime services from the already-validated manifest.

This keeps user-facing YAML flexible while keeping daemon startup deterministic
and testable.

## macOS and AppleScript

AppleScript support should stay app-specific by default. Apple Mail, Keynote,
Pages, Numbers, clipboard, notifications, and app control should expose narrow
tool kinds with stable targets such as `applemail://`, `keynote://frontmost`,
`pages://frontmost`, `numbers://frontmost`, and `macos://clipboard`.

Generic AppleScript is useful as an expert escape hatch and MCP-server building
substrate, but it should not be the default assistant authority. Keep first-use
approval, local-app Starlark tightening, source bindings, and explicit TCC
permissions enabled for practical personal-assistant use.
