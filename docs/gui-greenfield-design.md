# GUI greenfield design

This is the greenfield product design for a graphical CapableDeputy client.
It assumes the secure daemon, policy engine, MCP servers, audit log, approval
queue, and session graph remain the system of record. The GUI is a native
supervisory shell: it renders state, gathers user intent, relays explicit
approvals, and keeps the user in flow across desktop applications.

## Design thesis

CapDep should not be a full-screen chatbot. It should be a desktop control
plane for safe agency.

The best GUI is menu-bar-first, command-palette-driven, approval-centered, and
dashboard-backed:

- **Menu bar** for always-available status and entry.
- **Command palette** for quick work from any app.
- **Approval cards** for meaningful consent.
- **Small floating task panels** for live work beside the user's real apps.
- **Full dashboard** for supervision, setup, audit, policy, and recovery.

The app should feel like a careful assistant that can sit next to Mail,
Calendar, browser, Finder, Pages, Numbers, Keynote, and code tools. The user
should rarely need to switch into CapDep as a destination app unless they are
approving something, configuring trust, or investigating a policy decision.

## Primary users

### Primary: security-conscious desktop professional

This user wants a personal assistant that can actually do work without silently
leaking data or misusing authority.

Typical profile:

- Uses macOS as the daily desktop.
- Uses Gmail/Google Workspace and/or Apple Mail.
- Lives in Calendar, browser, Finder, documents, spreadsheets, slides, notes,
  and local Git repositories.
- Wants drafts, summaries, research, calendar planning, document edits, and
  workflow automation.
- Is willing to approve consequential actions when the UI makes the risk clear.
- Does not want to become a policy engineer to use the system.

### Secondary: technical power user

This user tunes policies and workflows.

Needs:

- Relationship groups.
- Approval patterns.
- Source bindings.
- Purpose defaults.
- Local model/provider configuration.
- Tool and MCP server status.

### Tertiary: reviewer/auditor/developer

This user inspects why CapDep allowed, denied, or escalated a workflow.

Needs:

- Audit trail.
- Policy trace.
- Provenance graph.
- Capability graph.
- Demo/replay mode.

The GUI should optimize daily surfaces for the primary user and isolate advanced
control in a dashboard/settings area.

## Primary use-case workflows

These should be easy, fast, and visible from the command palette or menu bar.

### Morning briefing

User intent:

- "Show me what matters this morning."

Inputs:

- Calendar.
- Gmail/Apple Mail.
- Notes.
- Selected projects.
- Optional web/news/search.

Outputs:

- Briefing summary.
- Conflicts.
- Urgent email.
- Action items.
- Draft replies.
- Scratch notes.

UX:

- Launch from menu bar or command palette.
- Results appear in a compact briefing panel.
- Drafts are clearly marked as drafts.
- Calendar writes or external-recipient drafts open approval cards only when
  policy requires them.

### Inbox triage

User intent:

- "Triage this inbox/thread."
- "Draft a reply to this email."

Inputs:

- Current Mail/Gmail thread or selected messages.
- Calendar availability.
- Relationship groups.

Outputs:

- Summary.
- Reply draft.
- Follow-up tasks.
- Suggested relationship-group or pattern updates after repeated approvals.

UX:

- Command palette over Mail/browser.
- Context chip shows attached thread.
- Result appears as a side panel or draft preview.
- Direct send remains hard to reach; draft is the easy path.

### Calendar planning

User intent:

- "Find time for this."
- "Schedule lunch with Alex."
- "Move my focus block."

Inputs:

- Calendar.
- People/contacts.
- Email context.

Outputs:

- Free-time suggestions.
- Conflict warnings.
- Proposed event.
- Self-calendar edits when safe.

UX:

- Side panel beside Calendar.
- Timeline preview before mutation.
- Self-only low-risk mutations can be smooth.
- External attendee changes require a readable approval card.

### Web research and synthesis

User intent:

- "Research this and summarize."
- "Compare these sources."
- "Turn this page into notes."

Inputs:

- Browser page.
- Search/fetch tools.
- Local notes/documents.

Outputs:

- Notes.
- Citations.
- Draft summary.
- Research memo.

UX:

- Browser context chip.
- Clear "external-untrusted" indicator for web content.
- Strong warning if the result is later proposed for email/send/shared docs.

### Writing and document work

User intent:

- "Rewrite this section."
- "Make a slide outline."
- "Append meeting notes to this document."
- "Summarize this spreadsheet."

Inputs:

- Frontmost Pages/Numbers/Keynote document.
- Selected files.
- Notes.

Outputs:

- Suggested edits.
- Applied bounded edits.
- Exported draft.
- Presentation outline.

UX:

- Command palette over the frontmost app.
- Diff/preview for substantial edits.
- First active local app edit is visible.
- Export/share/egress is gated when policy requires it.

### Sensitive egress review

User intent:

- "Send this."
- "Share this summary."
- "Draft a message using this data."

Inputs:

- Any labeled/sensitive/untrusted data.
- Proposed destination.

Outputs:

- Approval, denial, or safer alternative.

UX:

- Approval card is the product moment.
- Show source -> transformation -> destination.
- Explain labels and reason in plain language.
- Offer "draft only", "spawn clean session", "extract safe summary", or "deny".

### Policy recovery

User intent:

- "Why did CapDep block this?"
- "How can I do this safely?"

Inputs:

- Denied decision.
- Policy trace.

Outputs:

- Recovery path.
- Clean session.
- Declassification/extraction flow.
- Narrow capability grant or relationship group update, if appropriate.

UX:

- Denial cards should be as useful as approval cards.
- The user should never be stuck with "denied" and no next step.

## What should be easy

The easy path should align with CapDep's safety model.

- Start a purpose-scoped session.
- Ask about current app/selection/file/page.
- Summarize Mail/Gmail threads.
- Draft emails, without sending.
- Review Calendar and propose events.
- Create self-only calendar changes when policy allows.
- Summarize browser pages as untrusted source material.
- Create scratch notes.
- Read local project/document context.
- Ask "why was this blocked?"
- Approve or deny a single pending action after reviewing exact payload.
- Defer an approval.
- Open policy trace/provenance from an approval or denial.
- Replace placeholder relationship group addresses.
- Run setup checks for OAuth/TCC/local model/daemon status.

## What should be possible but not easy

These actions should be available but require more deliberate navigation,
review, or setup.

- Direct email send.
- Broad approval patterns.
- Destructive file/document edits.
- Clipboard read/write automation.
- Generic app/screen control.
- Policy override.
- Reading financial/health/legal/high-tier data in mixed workflows.
- Exporting/sending data influenced by untrusted web content.
- Adding a new MCP server.
- Editing Starlark policy.
- Changing source bindings and label assignments.
- Disabling first-use prompts.
- Running long unattended automation.
- Granting broad app automation permissions.

The design should not hide these actions. It should make them explicit,
auditable, and hard to do accidentally.

## Application posture

### Not full-screen first

CapDep should not default to full-screen. Full-screen is useful for demos,
audits, and deep investigations, but not for daily use.

Preferred posture:

- Menu bar always available.
- Command palette appears temporarily over the current app.
- Floating task panels can sit beside the active app.
- Approval cards come foreground when consent is required.
- Dashboard is a normal resizable window.

### Screen space strategy

Allocate screen space by user risk and task complexity:

| Need | Surface | Size | Persistence |
|---|---|---:|---|
| Status | Menu bar popover | Small | Always reachable |
| Quick intent | Command palette | Small | Temporary |
| Brief result | Floating panel | Small/medium | Dismissible |
| Draft/rewrite preview | Side panel | Medium | User-controlled |
| Approval | Approval card/window | Medium | Until decided/deferred |
| High-risk approval | Foreground review window | Large enough for payload | Until decided/deferred |
| Setup/configuration | Dashboard/settings | Full window | Normal app window |
| Audit/provenance | Dashboard/cockpit | Large/full optional | User-controlled |

### Always visible?

Only status should be always visible, and only as a menu-bar item. The assistant
itself should not occupy persistent screen space unless the user pins a task
panel.

Pinned panels should be optional for:

- Long-running research.
- Calendar planning.
- Draft review.
- Audit/demo monitoring.

Pinned panels should never obscure approval details or active app controls by
default.

## Interaction with the rest of the desktop

CapDep should integrate into existing desktop workflows instead of becoming a
separate workspace.

### Frontmost-app context

The command palette should detect and display:

- Frontmost app.
- Selected text, if available.
- Selected files, if Finder is active.
- Current browser page, if available.
- Frontmost Pages/Numbers/Keynote document.
- Current Mail thread/message.

The UI must show what is attached before the user submits.

### Context chips

Every prompt should display context chips:

- `Mail thread`
- `Calendar`
- `Chrome page: external-untrusted`
- `Pages document`
- `Finder selection: 3 files`
- `Clipboard`
- `Gmail account`
- `Purpose: Inbox`

Users should be able to remove chips before submission.

### Work result placement

Results should go where the user expects:

- Email work -> draft in Mail/Gmail or preview in CapDep.
- Calendar work -> proposed event preview, then Calendar write if approved.
- Research -> scratch note or research panel.
- Document work -> diff/preview, then bounded app edit if approved.
- Policy explanation -> approval/denial card or dashboard trace.

### App activation

CapDep should only bring itself forward when:

- User invokes it.
- Approval needs review.
- A long-running task completes and the user clicks a notification.
- Setup/configuration requires attention.

It should not steal focus for routine allowed tool calls.

## Automation model

Automation should be layered from most precise to least precise:

1. CapDep daemon/native tool.
2. Official remote MCP/API connector.
3. App-specific MCP/AppleScript tool.
4. Browser automation.
5. Generic screen/computer control.

The GUI should show which layer is being used because it changes reliability
and risk.

### Preferred automation

Use precise tools first:

- Gmail/Calendar/Drive/People MCP for Google data.
- Apple Mail-specific tools for mail.
- Pages/Numbers/Keynote-specific tools for documents.
- Filesystem/Finder-scoped tools for local files.
- Search/fetch tools for web research.

### High-friction automation

Generic screen control should be a special mode:

- Explicitly enabled per session.
- Visible on screen.
- Easy stop button.
- Per-app allow/block list.
- No sensitive apps by default.
- Higher prompt friction than app-specific tools.

### Stop control

Every long-running automation panel needs:

- Stop now.
- Pause.
- Show last action.
- Show next intended action.
- Open policy trace.

## Main UI surfaces

### Menu bar popover

Contents:

- Connection status.
- Local model/provider status.
- Current profile.
- Pending approvals count.
- Active sessions count.
- OAuth/TCC warnings.
- Buttons:
  - New Ask
  - New Session
  - Approvals
  - Dashboard
  - Setup
  - Pause Automation
  - Quit

Do not include:

- High-risk approve buttons.
- Full policy editing.
- Verbose audit logs.

### Command palette

Default layout:

- Top: current purpose and frontmost app.
- Middle: text input.
- Context chips below input.
- Suggested workflows based on app context.
- Footer: model/daemon status and keyboard hints.

Suggested actions by context:

- Mail: summarize, draft reply, extract tasks, classify urgency.
- Calendar: find time, schedule, explain conflicts, prepare meeting.
- Browser: summarize page, research topic, capture source as untrusted.
- Finder: summarize selected files, compare documents, create note.
- Pages: revise, summarize, append notes, export draft.
- Numbers: summarize sheet, find anomalies, create chart notes.
- Keynote: outline deck, speaker notes, present/check deck.
- Terminal/code editor: explain error, inspect repo, draft patch plan.

### Approval card

Default layout:

- Header: action, severity, status.
- Target: exact destination/resource.
- Effect: read/draft/send/calendar/write/destructive/app-control.
- Source summary: data used and labels.
- Policy reason: plain English plus rule id.
- Payload preview: exact content or exact tool args.
- Alternatives: safer paths.
- Actions:
  - Deny
  - Defer
  - Approve Once
  - Approve Group, when daemon grouped siblings
  - Create Narrow Pattern, when safe
  - Add Relationship, when applicable
  - Show Trace

Approval copy should be concrete:

- Good: "Create Gmail draft to spouse@example.com using your calendar summary."
- Bad: "Allow GMAIL_DRAFT?"

### Task panel

Use for active work that should stay beside another app.

Contents:

- Goal.
- Current step.
- Attached context.
- Recent actions.
- Pending approval indicator.
- Stop/Pause controls.
- Compact result area.

Task panels can be pinned but should not be always-on by default.

### Dashboard / security cockpit

Top-level areas:

- Today.
- Approvals.
- Sessions.
- Workflows.
- Policy trace.
- Provenance.
- Trust.
- Setup.
- Settings.

Large layout:

- Left rail: top-level areas and active sessions.
- Center: selected session/approval/workflow.
- Right inspector: labels, capabilities, provenance, policy trace.

Medium layout:

- Left rail plus content.
- Inspector collapses into a tab.

Small layout:

- Single column.
- Approval payload and actions remain above fold.

## Integrated menu system

The GUI should implement a real macOS menu system, not just a menu bar extra.

### CapDep menu

- About CapDep
- Check for Updates
- Settings
- Setup Assistant
- Services
- Hide CapDep
- Hide Others
- Show All
- Quit CapDep

### File

- New Ask
- New Session
- New Purpose Session
- Open Dashboard
- Open Approval Queue
- Open Audit Log
- Export Audit Bundle
- Close Window

### Edit

- Undo
- Redo
- Cut
- Copy
- Paste
- Select All
- Copy Approval Payload
- Copy Policy Trace
- Copy Session ID

### View

- Show Menu Bar Popover
- Show Command Palette
- Show Dashboard
- Show Approvals
- Show Sessions
- Show Provenance
- Show Policy Trace
- Toggle Inspector
- Enter Full Screen

### Session

- Start Session
- Fork Clean Session
- Pause Session
- Resume Session
- Abort Session
- Set Purpose
- Add Label
- Grant Capability
- Revoke Capability
- Toggle First-Use Prompts
- Toggle Shadow Mode, advanced/debug only

### Approvals

- Show Pending Approvals
- Approve Once
- Deny
- Defer
- Approve Group
- Create Narrow Pattern
- Revoke Pattern
- Add Recipient to Relationship Group
- Require Touch ID for High-Risk Approvals

### Workflows

- Morning Briefing
- Inbox Triage
- Draft Reply
- Calendar Planning
- Meeting Prep
- Web Research
- Summarize Selection
- Summarize Selected Files
- Revise Frontmost Document
- Create Scratch Note

### Automation

- Pause All Automation
- Stop Current Task
- Show Last Action
- Show Next Intended Action
- Manage App Permissions
- Manage Blocked Apps
- Enable Screen Control for This Session, advanced and high-friction
- Disable Screen Control

### Trust

- Relationship Groups
- Approval Patterns
- Source Bindings
- OAuth Accounts
- App Permissions
- Model Backend
- Policy Bundle
- Validate Configuration

### Window

- Minimize
- Zoom
- Bring All to Front
- Dashboard
- Approvals
- Current Task Panel

### Help

- CapDep Help
- Keyboard Shortcuts
- Explain Security Model
- Why Was This Blocked?
- Run Smoke Test
- Open Logs Folder
- Report Issue

## Settings structure

Settings should be separated by user mental model:

- General
  - launch at login
  - default purpose
  - keyboard shortcut
  - notification preferences
- Assistant
  - model backend
  - local MLX status
  - hosted provider status
  - thinking mode defaults
- Accounts
  - Google OAuth
  - Apple Mail
  - future providers
- Automation
  - app permissions
  - per-app allow/block
  - screen-control mode
  - browser automation
- Trust
  - relationship groups
  - approval patterns
  - high-risk approval signing
- Policy
  - policy bundle
  - purposes
  - source bindings
  - Starlark inspectors
  - validation results
- Audit
  - audit log location
  - retention/export
  - provenance storage
- Advanced
  - socket path
  - daemon config path
  - MCP server status
  - debug logging

## Empty states and first-run experience

First launch should not open to an empty dashboard. It should open Setup
Assistant.

Setup steps:

1. Start/connect daemon.
2. Choose local MLX or hosted provider.
3. Configure Google OAuth if desired.
4. Check Apple app permissions.
5. Replace placeholder self/family/work addresses.
6. Run a smoke workflow.
7. Teach the core model: "CapDep drafts easily, sends deliberately."

Empty approval queue:

- "No pending approvals. CapDep will ask when a proposed action crosses a
  policy boundary."

Offline daemon:

- Show socket path.
- Start daemon button.
- Open logs.
- Run diagnostics.

Missing app permission:

- Explain which workflow needs it.
- Provide "Open System Settings" or "Try again" action.

## Visual hierarchy

Daily UI should prioritize:

1. User goal.
2. Attached context.
3. Proposed action/result.
4. Risk labels and policy reason.
5. Advanced trace details.

Do not lead with internal model names. Translate them:

- Bell-LaPadula -> "This would send sensitive data to a lower-trust place."
- Biba -> "This source is less trusted than the destination."
- Brewer-Nash -> "This session has conflicting compartments."
- Object capability -> "This session was not granted that authority."
- Clark-Wilson -> "This action requires your explicit approval."

Rule ids should remain visible in an expandable detail row.

## Keyboard and accessibility

Required shortcuts:

- Global command palette.
- Open approvals.
- Approve once, only while focused in approval card.
- Deny.
- Defer.
- Show trace.
- Stop automation.

Accessibility:

- Full keyboard navigation.
- VoiceOver labels for action, target, risk, and payload.
- System colors and contrast.
- No color-only risk indication.
- Dynamic Type where feasible.
- Reduced-motion support.

## Product guardrails

The GUI must not:

- Make policy decisions client-side.
- Hide exact payloads before approval.
- Allow notification-only approval of high-risk actions.
- Make direct send easier than draft.
- Make broad automation permission the default.
- Encourage always-on screen control.
- Let the agent invoke approval commands.
- Autogenerate trust rules without human action.

The GUI should:

- Prefer drafts.
- Prefer narrow patterns.
- Prefer relationship-group-based friction reduction.
- Prefer API/MCP over screen control.
- Make denials recoverable.
- Make audit/provenance inspectable.

## Greenfield product roadmap

### Phase 1: Daily shell

- Menu bar status.
- Command palette.
- Dashboard shell.
- Approval cards.
- Session list.
- Audit event list.

### Phase 2: Practical workflows

- Morning briefing.
- Inbox triage.
- Calendar planning.
- Browser research.
- Finder selected-file summary.
- Frontmost Pages/Numbers/Keynote workflows.

### Phase 3: Trust and setup

- Setup Assistant.
- OAuth status/login.
- TCC permission checks.
- Relationship group editor.
- Approval pattern editor.
- Policy validation.

### Phase 4: Advanced supervision

- Provenance graph.
- Policy trace explorer.
- Capability editor.
- Workflow replay.
- Audit export.
- Touch ID approval signing.

### Phase 5: Desktop extensions

- Share extension.
- Finder extension.
- Services menu.
- Shortcuts/App Intents.
- Native notifications with safe deep links.

## Final product shape

CapDep should be a small app most of the time and a big app only when it needs
to be.

Daily interaction should happen over the user's existing desktop, through the
menu bar, command palette, and task panels. Consequential actions should expand
into focused approval cards. The full dashboard should exist for supervision,
configuration, and audit, not as the required home for every task.

The signature experience is:

> Ask from anywhere. See exactly what context is attached. Let CapDep draft and
> organize easily. Require clear consent for sending, sharing, destructive
> writes, high-tier data, and generic desktop control. Keep a readable audit
> trail for everything.
