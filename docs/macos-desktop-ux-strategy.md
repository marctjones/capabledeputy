# macOS desktop UX strategy

This document translates current desktop UX guidance and current desktop-agent
patterns into a product direction for CapableDeputy's macOS app.

CapDep is not a general chat app. It is a supervised desktop-agent runtime whose
main value is that every useful action crosses a deterministic policy
chokepoint. The UI must therefore optimize for two things at once:

1. Keep the user in flow across Mail, Calendar, Finder, browser, Pages,
   Numbers, Keynote, Google Workspace, and local files.
2. Make sensitive boundaries visible at the moment they matter, without turning
   every action into an approval chore.

## Source guidance

The most relevant guidance comes from four places:

- Apple Human Interface Guidelines: macOS apps should use familiar system
  patterns for windows, menus, toolbars, colors, alerts, scrolling, settings,
  and file workflows. The HIG emphasizes discoverable commands, keyboard
  shortcuts, standard system behavior, and direct alert copy.
- Microsoft Windows / Fluent guidance: desktop apps should be intuitive,
  consistent, responsive across window sizes, and use hierarchy, color, motion,
  layering, typography, and spacing to focus attention rather than decorate.
- ChatGPT desktop: the important desktop pattern is fast invocation from any
  screen, app-context awareness, screenshots/files/web search, and preserving
  workflow continuity while the user works elsewhere.
- Claude Cowork / computer use: the important agent pattern is ordered access:
  prefer precise connectors, then browser/app integrations, then screen
  interaction. Claude's safety docs also highlight explicit per-app permission,
  user review for consequential actions, and the risks of direct screen control.

References:

- Apple HIG overview: <https://developer.apple.com/design/human-interface-guidelines>
- Apple menu bar: <https://developer.apple.com/design/Human-Interface-Guidelines/the-menu-bar>
- Apple windows: <https://developer.apple.com/design/human-interface-guidelines/windows>
- Apple alerts: <https://developer.apple.com/design/human-interface-guidelines/alerts>
- Apple color: <https://developer.apple.com/design/human-interface-guidelines/color>
- Apple toolbars: <https://developer.apple.com/design/human-interface-guidelines/toolbars>
- Microsoft Windows design overview: <https://learn.microsoft.com/en-us/windows/apps/design/>
- Microsoft Windows design guidelines: <https://learn.microsoft.com/en-us/windows/apps/design/guidelines-overview>
- Microsoft layout overview: <https://learn.microsoft.com/en-us/windows/apps/design/layout/>
- Microsoft responsive design: <https://learn.microsoft.com/en-us/windows/apps/design/layout/responsive-design>
- Microsoft screen breakpoints: <https://learn.microsoft.com/en-us/windows/apps/design/layout/screen-sizes-and-breakpoints-for-responsive-design>
- Microsoft color: <https://learn.microsoft.com/en-us/windows/apps/design/signature-experiences/color>
- Microsoft typography: <https://learn.microsoft.com/en-us/windows/apps/design/signature-experiences/typography>
- Microsoft motion: <https://learn.microsoft.com/en-us/windows/apps/design/signature-experiences/motion>
- Microsoft layering/elevation: <https://learn.microsoft.com/en-us/windows/apps/design/signature-experiences/layering>
- ChatGPT desktop: <https://chatgpt.com/features/desktop/>
- ChatGPT Work with Apps on macOS: <https://help.openai.com/en/articles/10119604-work-with-apps-on-macos>
- Claude Cowork computer use: <https://support.claude.com/en/articles/14128542-let-claude-use-your-computer-in-cowork>
- Claude computer-use tool docs: <https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool>

## Primary user

The primary user is a security-conscious professional who lives in desktop
productivity apps and wants a personal assistant that can actually act:

- macOS-first, often Apple Silicon.
- Uses Google Workspace and Gmail or Apple Mail.
- Uses Calendar heavily.
- Works with local files, browser research, documents, spreadsheets, slides,
  notes, and code repositories.
- Wants low-friction help, but does not want silent data leakage, ambient
  authority abuse, or accidental irreversible actions.
- Is willing to review meaningful approvals if the review explains exactly what
  data, destination, and effect are involved.

Secondary users:

- Developers evaluating CapDep's security model.
- Security/privacy reviewers inspecting audit traces.
- Power users configuring personal workflow policies.

The primary UX must serve the first group. The cockpit/audit/debug surfaces
serve the second and third groups.

## Product posture

CapDep should feel less like a full-screen app and more like a trusted desktop
control plane:

- Fast to summon.
- Mostly out of the way.
- Always able to explain what it is doing.
- Visible when risk rises.
- Native enough that macOS permissions, windows, notifications, keyboard
  shortcuts, and app integrations feel normal.

The mental model is:

- Raycast-style invocation.
- 1Password-style review of sensitive operations.
- Little Snitch-style visibility into flows and destinations.
- Shortcuts-style user-owned automations.
- Console/Activity Monitor-style traceability when investigating.

## Interface surfaces

### 1. Menu bar controller

This should be the default always-available surface.

Use it for:

- Daemon/model status.
- Pending approval count.
- Current profile and purpose.
- Start/stop daemon.
- New session.
- Open command palette.
- Open approvals.
- Open dashboard.
- Open setup/settings.

Design requirements:

- Compact; do not become a mini dashboard.
- Show status at a glance: connected/offline, local model, pending approvals,
  OAuth/TCC health.
- Never put high-risk approve buttons directly in the menu bar. Use it to open
  the approval card.

Screen-space posture:

- Lives in the menu bar.
- Opens a narrow popover.
- Dismisses quickly.
- Does not stay on top by default.

### 2. Global command palette

This should be the main daily interaction surface.

Use it for:

- "Summarize selected Mail thread."
- "Draft reply."
- "Plan my calendar."
- "Summarize this browser page."
- "Extract action items from selected text."
- "Revise the frontmost Pages document."
- "Make a Keynote outline from these notes."
- "Create scratch note."
- "Ask about frontmost app."

Design requirements:

- Global hotkey.
- Small centered or upper-third floating panel.
- Context banner: "Working with Mail thread", "Working with Chrome page",
  "No app context attached".
- Purpose selector: General, Inbox, Calendar, Writing, Research.
- Explicit context chips: selected file, current app, current page, current
  document, clipboard, etc.
- A visible warning when context is untrusted or sensitive.

Screen-space posture:

- Temporary overlay.
- Can float above other apps while composing.
- Should not require full-screen.
- Should not steal focus for long workflows after submission.

### 3. Approval cards

Approval cards are CapDep's most important UI. They are where the security
model becomes usable.

Each card must answer:

- What action is proposed?
- What exact target will be affected?
- Is it read, reversible write, irreversible write, egress, purchase, calendar
  mutation, app automation, or desktop control?
- What data influenced the action?
- Which labels/provenance are involved?
- Why did policy require approval?
- What will happen if approved?
- What safer alternatives exist?
- Is this a one-time approval, a pattern candidate, or a structural deny?

Required actions:

- Approve once.
- Deny.
- Defer.
- Copy payload.
- Show policy trace.
- Show provenance.
- Spawn clean session.
- Extract/declassify safe summary when applicable.
- Add exact approval pattern when safe and narrow.
- Add recipient to relationship group when appropriate.

Screen-space posture:

- High-risk approval: foreground sheet/window, never notification-only.
- Low-risk reversible approval: compact card with quick action.
- Grouped approvals: list with per-item expansion and "approve all safe
  siblings" only when the daemon has grouped them.

### 4. Security cockpit

This is the full desktop window.

Use it for:

- Sessions.
- Pending and historical approvals.
- Live audit events.
- Policy trace.
- Labels and provenance.
- Capability grants and revocations.
- Relationship groups.
- Approval patterns.
- OAuth/TCC/setup status.
- Workflow demos and diagnostics.

Design requirements:

- Three-column layout at large widths:
  - Left: sessions, purposes, approval queue.
  - Center: selected session conversation/workflow/approval.
  - Right: policy trace, labels, provenance, capabilities.
- Collapse to two columns at medium widths.
- Single column at narrow widths.
- Use color sparingly for severity and state, not decoration.
- Make "why was this blocked?" as prominent as "what happened?"

Screen-space posture:

- Normal resizable macOS window.
- Useful side-by-side with Mail/browser/Calendar.
- Should not assume full-screen.
- Full-screen is optional for audit review or demos.

### 5. Setup and trust wizard

This is required before CapDep is practical for non-developers.

Use it for:

- Start daemon at login.
- Choose local MLX or hosted model.
- Run Google OAuth.
- Check AppleScript/TCC Automation permissions.
- Configure Mail, Pages, Numbers, Keynote, Finder, Calendar surfaces.
- Replace placeholder addresses in relationship groups.
- Validate policy files.
- Run a smoke workflow.

Design requirements:

- Show missing permissions as fixable checklist items.
- Explain why each permission is needed.
- Separate "read context" permissions from "write/control app" permissions.
- Do not request every permission upfront. Ask in workflow order.

### 6. Notifications

Use notifications for:

- Approval requested.
- Long-running task completed.
- Daemon/model offline.
- OAuth expired.
- Automation permission missing.

Do not use notifications for:

- High-risk approval without opening a full approval card.
- Policy override.
- Anything where the payload must be read before consent.

### 7. Share, Services, Finder, and Shortcuts

These should be second-wave integrations after the core shell is stable.

Use them for user-initiated context injection:

- Share selected text/page/file to a CapDep session.
- Finder action: summarize selected files or create scoped research session.
- Services menu: ask CapDep about selection.
- Shortcuts actions: start named CapDep workflow, open approval queue, run
  daily briefing.

Important boundary:

- These integrations may relay user intent and context.
- They must not bypass daemon policy.
- They must not approve actions automatically.

## Desktop-agent workflow model

Claude's ordering is the right default for CapDep too, with CapDep-specific
security language:

1. Use precise connector or MCP API when available.
2. Use app-specific AppleScript/MCP automation.
3. Use browser automation only when needed.
4. Use generic screen interaction only as a last resort.

CapDep should make this visible:

- "Using Gmail MCP because it is precise and auditable."
- "Using Apple Mail draft tool; no direct send authority."
- "Using browser view; page is labeled external-untrusted."
- "Using screen interaction; this is slower and less reliable."

Screen control should be treated as a high-friction mode because it exposes
visible desktop data and is less semantically precise than MCP/tool APIs.

## Main CapDep workflows

### Morning briefing

Surface:

- Menu bar or command palette.

Flow:

- Read Calendar, Gmail/Apple Mail, notes, and selected news/search sources.
- Summarize schedule, urgent email, conflicts, action items.
- Create drafts and scratch notes.
- Do not send mail automatically.

Screen-space:

- Results in a compact window or dashboard card.
- Approval cards only if it creates calendar changes, drafts to external
  recipients with sensitive content, or other gated effects.

### Inbox triage

Surface:

- Command palette over Mail or dashboard session.

Flow:

- Summarize thread.
- Classify urgent/needs reply/FYI.
- Draft replies.
- Suggest relationship group/pattern only after repeated approved use.

Screen-space:

- Small overlay while reading Mail.
- Approval detail only for draft creation when required.

### Calendar planning

Surface:

- Command palette over Calendar or dashboard.

Flow:

- Find free time.
- Detect conflicts.
- Draft proposed events.
- Mutate self-calendar with low friction when policy allows.
- Gate external attendees and sensitive data.

Screen-space:

- Side panel beside Calendar.
- Timeline preview before mutation.

### Writing and document editing

Surface:

- Command palette over Pages/Numbers/Keynote or Finder.

Flow:

- Read frontmost document.
- Suggest edits.
- Apply bounded edits or export drafts.
- Gate first active document edit/export and high-tier data flows.

Screen-space:

- Overlay for instruction.
- Diff/preview window for substantial edits.

### Web research

Surface:

- Command palette over browser or dashboard.

Flow:

- Fetch/read external content.
- Label as untrusted.
- Summarize locally.
- Block or gate egress that combines untrusted content with sensitive internal
  data.

Screen-space:

- Compact research panel.
- Clear "untrusted content attached" indicator.

### Sensitive egress review

Surface:

- Approval card.

Flow:

- Explain why information-flow policy gated the action.
- Show source data labels/provenance and destination.
- Offer spawn clean session / extract safe summary / deny.

Screen-space:

- Foreground approval window.
- Payload must be readable before approval.

### Policy tuning

Surface:

- Security cockpit settings.

Flow:

- Edit relationship groups.
- Review approval patterns.
- Inspect frequency caps and first-use prompts.
- Validate Starlark/policy config.

Screen-space:

- Full dashboard, not menu bar.

## Screen-space rules

Use the smallest surface that preserves informed consent:

| Situation | Surface | Persistence |
|---|---|---|
| Quick ask from current app | Command palette | Temporary |
| Daemon status / pending count | Menu bar | Always available |
| Low-risk result | Compact panel | Dismissible |
| Approval required | Approval card | Foreground until decided/deferred |
| High-risk approval or override | Full approval window | Foreground, explicit action |
| Long-running task | Menu bar + notification | Background |
| Audit/debug/policy tuning | Security cockpit | Normal resizable window |
| Demo/security review | Security cockpit | Optional full-screen |

Avoid:

- Always-on floating chat windows by default.
- Notification-only approval for consequential actions.
- Full-screen-first design.
- Hiding provenance/policy behind developer-only logs.
- Putting direct send/destructive actions in quick command surfaces without a
  daemon approval path.

## Information architecture

Recommended top-level dashboard sections:

- Today
  - active sessions
  - pending approvals
  - recent blocks
  - daemon/model status
- Approvals
  - pending
  - deferred
  - approved/denied history
  - grouped approvals
- Sessions
  - conversation/workflow
  - purpose
  - labels
  - capabilities
  - trace
- Flows
  - provenance graph
  - source/destination
  - policy decision reason
- Trust
  - relationship groups
  - approval patterns
  - OAuth accounts
  - app permissions
- Settings
  - daemon startup
  - model backend
  - policy bundle
  - notifications
  - keyboard shortcut

## Visual design principles

Use native macOS styling as the base:

- System fonts and dynamic type.
- System colors that adapt to light/dark mode.
- Standard keyboard shortcuts.
- Standard window, toolbar, menu, and settings behavior.
- Native alert tone: direct, neutral, specific.

CapDep-specific visual language:

- Labels as chips: personal, financial, health, external-untrusted, restricted.
- Effect class icons: read, draft, send, calendar mutate, destructive, app
  control.
- Provenance arrows: source -> transformation -> destination.
- Severity color:
  - neutral/blue: informational
  - yellow: review required
  - red: denied/destructive/high-risk
  - green: allowed/completed
- Use motion only for state continuity: approval appears from pending queue,
  task moves to completed, policy trace expands from decision. Avoid decorative
  animation.

## Interaction principles

- The user drives consent. The agent never sees or controls approval commands.
- Every approval must show exact payload and target.
- Prefer reversible actions and drafts over committing actions.
- Prefer precise APIs over screen interaction.
- Keep the user in their current app when possible.
- Escalate surface size with risk, not with implementation complexity.
- Expose a recovery path for denials.
- Let users create narrow patterns from repeated safe approvals.
- Make current purpose visible because purpose changes policy behavior.
- Make attached context visible before submission.

## Implementation priorities

### P0: Native shell foundation

Already started:

- SwiftUI menu bar app.
- Dashboard window.
- JSON-RPC Unix socket client.
- Sessions, approvals, audit polling.
- Approve/deny from native approval detail.

Next:

- Replace polling with audit subscription stream.
- Add stable GUI-focused daemon RPCs where current generic RPCs are too thin.
- Add settings window for socket path and daemon config.
- Add app icon, bundle metadata, signing/notarization path.

### P1: Approval-quality upgrade

- Approval card with policy explanation.
- Source labels and provenance summary.
- Effect preview.
- Safer alternatives.
- Pattern suggestion.
- Relationship group suggestion.
- Grouped approval UI.

### P2: Command palette and app context

- Global hotkey.
- Frontmost app detection.
- Context chips.
- Purpose selector.
- Workflow templates for Mail, Calendar, browser, Finder, Pages, Numbers,
  Keynote.

### P3: Setup wizard

- Daemon launch agent.
- MLX/local model check.
- Google OAuth status and login.
- Apple Automation/TCC checklist.
- Relationship group setup.
- Policy validation.
- Smoke workflow.

### P4: macOS integrations

- Notifications.
- Share extension.
- Services actions.
- Finder extension.
- Shortcuts/App Intents.
- Touch ID / LocalAuthentication for high-risk approvals.

### P5: Advanced cockpit

- Provenance DAG.
- Policy trace explorer.
- Approval/pattern analytics.
- Workflow replay.
- Audit export.

## Deliberate non-goals for the first macOS app

- Do not implement policy decisions in Swift.
- Do not embed the Python engine into the app bundle until the daemon lifecycle
  is stable.
- Do not add generic screen control as the default path.
- Do not make full-screen chat the primary interaction.
- Do not optimize for Windows before the macOS shell is coherent.

## Summary recommendation

CapDep should be a menu-bar-first, command-palette-driven, approval-centered
macOS assistant.

The full dashboard is necessary, but it should not be the primary daily
surface. Daily use should happen over the user's existing apps. The dashboard
should appear when the user needs supervision, explanation, setup, or audit.

The UI should allocate screen space according to risk:

- Status: menu bar.
- Fast intent: command palette.
- Routine result: compact panel.
- Consequential action: approval card.
- Investigation/configuration: dashboard.

This is the best alignment between desktop UX practice, current agent product
patterns, and CapDep's security model.
