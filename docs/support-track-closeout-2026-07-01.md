# Support Track Closeout — 2026-07-01

This note records the closure evidence for the supporting milestones that sit
outside the product ladder.

## 07 Support — Source Identity and Labeling Correctness

Status: complete.

- #51 is implemented by `substrate/google_source.py` and covered by
  `tests/test_google_source_ports.py`: Gmail messages/recipients, Google Drive
  file IDs, and Google Calendar event IDs canonicalize from stable IDs and
  stable URL forms, while ambiguous strings fail closed.
- #139 is implemented by `policy/email_labeling.py`, active
  `configs/email_label_rules.yaml`, and `tests/policy/test_email_labeling.py`.
  Rules are raise-only and merge per-message labels with the server-wide floor.
- #42 is closed by the child work above plus the existing catalog-aware tier and
  filesystem-labeling paths.

## 08 Support — Terminal UX and Approval Polish

Status: complete for the terminal support track.

- #16: markdown rendering, Alt-Enter multiline input, contextual completion,
  OSC 52 clipboard, tool summaries, and streaming turn lifecycle support are in
  the line REPL and tested across `tests/test_repl_ui_helpers.py`,
  `tests/test_markdown_media.py`, `tests/test_repl_smoke_e2e.py`, and the
  v0.33 lifecycle tests.
- #17: the product ladder now prioritizes CapDepMac as the primary workspace.
  Terminal viewer scope is limited to the existing trace/audit/copy/status
  views and rich-mode sidebar; a full split-pane terminal workspace is not a
  prerequisite for the daemon safety roadmap.
- #19: terminal capability detection and trusted inline image emission exist in
  `cli/terminal_caps.py`, `cli/terminal_graphics.py`, and
  `cli/markdown_media.py`, with graceful fallback and tests.
- #27: approvals are surfaced inline at the chokepoint, the toolbar shows a
  pending-approval hint, and the `a` key opens review without taking away the
  normal message prompt.
- #29: toolbar clipping uses `wcwidth`; narrow terminal warnings are shown in
  the toolbar and covered by UI-helper tests.

## 09 Research — Non-Goals and Safe Alternatives

Status: complete as research decisions.

- #178: remote/mobile daemon control remains out of scope unless a future
  security model explicitly approves a network listener. Current safe
  alternative: local notifications, local deep links, and signed handoff
  artifacts.
- #179: broad always-on autonomous action remains out of scope. Current safe
  alternatives: narrow grants, workflow templates, onguard queues/schedules,
  first-use prompts, and exact-payload approval groups.
- #180: community marketplaces remain untrusted-by-default. Current safe
  alternative: daemon admission, mapping fingerprints, explicit approval, and
  provider conformance tests.
- #181: web/cross-platform GUI alternatives are deferred unless they remain thin
  local IPC clients. CapDepMac remains the primary desktop product surface.

## 10 Backlog — Formal Models and Deferred Breadth

Status: complete.

- #58 is implemented by `label_join` and `label_dominates` in
  `policy/labels.py`, with property/unit tests in `tests/policy/test_label_state.py`.
- #59 is implemented by `SessionGraph.revoke_capability(..., eager_teardown=True)`,
  daemon/CLI/MCP exposure, audit payloads listing removed descendants, and
  `tests/test_cascade_revocation.py`.
- #45 is closed by the two child items above and the updated
  `docs/security-models.md` model table.
