# CapDep Test Work List

This is the standing test plan for deciding where new coverage belongs. The
default standard suite must stay deterministic, local, and safe with test data.
Real AI, real image models, real credentials, and real user data remain opt-in
smokes.

## Standard Deterministic Suite

- **Swift GUI app-process wiring:** no-focus `CAPDEP_GUI_TEST_COMMAND_FILE`
  hook, prompt submission through the real app model, stable chat accessibility
  hook declarations, failure artifacts, and Swift source coverage reporting.
- **Prompt status UX contracts:** queued, running, completed, failed, and
  recovered prompt-run state in Swift model tests and no-focus GUI smoke,
  including multi-prompt queueing before earlier results return.
- **Daemon image jobs:** profile/readiness, queued/loading/running/finalizing,
  completed, failed, canceled, event cursors, job listing, and actionable
  failure messages.
- **Image rendering contracts:** markdown/image attachment parsing, generated
  work-image authorization, hallucinated path repair, local image URL resolution,
  and unsupported/oversized image refusal.
- **Security and flow patterns:** policy decisions, labels, provenance,
  declassification, approval/grant recovery, tainted session visibility, prompt
  injection rendering safety, and clean-session recovery.
- **MCP contract tests:** bundled server discovery, schemas, policy
  classification, label/provenance propagation, fail-closed unknown tools, and
  daemon dispatch.

## Opt-In Real AI Smokes

- **Real text model smoke:** one fast/default model completes chat, streams
  tokens, can be canceled, and can produce a valid safe tool call.
- **Real model matrix smoke:** only run multiple models for surfaces that differ
  by model behavior: tool-call syntax, streaming, refusal handling, latency, and
  context carryover.
- **Real MCP plus AI smoke:** a real model chooses a safe read-only MCP tool,
  daemon policy classifies it, the tool result returns, and labels/provenance are
  attached.
- **Real image smoke:** selected image backend reports readiness, generates a
  file, emits status/progress events, and CapDepMac can render or at least
  trace a completed image-markdown result.

## Data Rules

- Default tests use test data only.
- Real data smokes must be read-only unless explicitly named as mutation tests.
- Real credential tests must be skipped by default and enabled only through
  explicit environment variables.
- GUI tests should prove display and app-process wiring; daemon/policy tests own
  correctness of security decisions.
