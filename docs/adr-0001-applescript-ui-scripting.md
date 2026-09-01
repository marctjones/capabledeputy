# ADR 0001 — AppleScript scripting surface on the CapDepMac UI

**Status:** Accepted (owner decision, 2026-09-01)
**Decider:** Project owner

## Decision

CapDepMac exposes a native AppleScript dictionary — including approval
decide commands — and the scripting surface **ships in production builds**,
not only in test builds. The owner made this decision explicitly, with the
trade-off surfaced: the goal is robust scripted interactive test automation,
and the scripting surface is treated as independent of the runtime's other
controls and policies. The app also stays Swift, restructured as a standard
Xcode-openable app rather than being rewritten cross-platform.

## What shipped

- The SwiftPM package splits into the `CapDepMac` library target (all app
  code), a thin `Launcher/` executable target (preserves `swift build` /
  `run-local-app.sh`), and the committed Xcode app shell in
  `apps/macos/CapDep/App/CapDep.xcodeproj` (generated from `App/project.yml`
  via XcodeGen; regenerate only when the spec changes). The Xcode build is
  the **canonical scripting path**; the script-assembled dev bundle also
  carries the scripting keys but uses a shell-wrapper launcher, so prefer
  the Xcode build when debugging Apple-event targeting.
- Terminology lives in `apps/macos/CapDep/CapDep.sdef`; implementation in
  `Sources/AppleScriptSupport.swift`. Surface: read-only properties
  (`daemon connected`, `current session id`, `pending approval ids`) and
  commands (`send prompt`, `approve approval`, `deny approval`,
  `refresh state`). Commands suspend/resume so osascript calls are
  synchronous — the property that makes scripted tests deterministic.
- `Tests/AppleScriptSupportTests.swift` pins the sdef ↔ ObjC-runtime name
  contract (drift there fails silently at event time otherwise).
- `scripts/verify-applescript.sh` is the GUI-sensitive smoke: it needs a
  running app plus an Automation/TCC grant, so it belongs to the
  release-checklist "GUI-sensitive smokes on a permissioned machine"
  bucket, not headless CI.

## Consequences and boundary

Any local process that can send Apple events to the app can drive these
commands, including `approve approval`. The daemon-side policy engine,
approval floors, and audit trail are unaffected by this surface — an
AppleScript approve is recorded exactly like a clicked approve — but the
"human present at the approval card" property is no longer implied by an
approval event. This is the accepted trade-off, by owner decision; it is
recorded here rather than mitigated in code. Revisit before any
distribution/packaging milestone (#310, #342) widens the install base
beyond the owner's own machines.
