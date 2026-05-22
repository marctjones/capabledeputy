# CapDep operator runbook

Everything an operator needs to drive capdep in normal use. Reflects
the v0.5 — UX EPIC features landed in the May 2026 sprint.

---

## Quick start

```bash
# One-time setup
npm install -g @googleworkspace/cli && gws auth setup && gws auth login -s drive,gmail,calendar,docs,sheets
npm install -g gws-mcp-server
uv run capdep imap-setup            # Gmail via App Password (no OAuth)
uv run capdep gworkspace-setup      # Workspace via the official CLI

# Daily use
uv run capdep chat
```

The daemon auto-starts if not running. `~/.config/capabledeputy/daemon.yaml`
is loaded by default (`imap-setup` / `gworkspace-setup` populate it).

---

## Surface selection — `--mode`

| Flag | Behavior |
|---|---|
| `capdep chat` | Auto-detect. Rich Textual surface on Ghostty / kitty / iTerm2 / WezTerm / Alacritty; line-oriented prompt-toolkit REPL elsewhere. |
| `capdep chat --mode line` | Force line mode. Use over ssh, in scripts, or when you prefer the line REPL. |
| `capdep chat --mode rich` | Force rich mode. Errors if no TTY. |
| `capdep chat --mode auto` | Same as the default. |

Detection is by `$TERM_PROGRAM` / `$TERM` heuristics — see `cli/terminal_caps.py`.

---

## Slash commands

### Session control
| Command | Purpose |
|---|---|
| `/sessions` | List all sessions (clickable IDs on modern terminals) |
| `/session [id]` | Show details for current session or one by id |
| `/switch <id>` | Retarget the REPL to another session |
| `/whoami` | Print current session id |
| `/spawn <intent> [--bare]` | Create a clean child session, switch to it. Inherits non-destructive caps unless `--bare`. |
| `/abort [id]` | Abort current session (or one by id) |
| `/grant <KIND> <pattern> [flags]` | Grant a capability. Flags: `--one-shot`, `--destructive`, `--ttl SECONDS`, `--rate MAX/WINDOW`, `--max-amount N` |
| `/status` / `/labels` / `/caps` | Inspect current session state |

### Approval flow
| Command | Purpose |
|---|---|
| `/approvals` | List pending approvals (clickable IDs) |
| `/approve <id>` | Approve a queued action |
| `/deny <id>` | Deny a queued action |
| `/submit` | Manually submit an approval |
| `/remember <ACTION> <target> [flags]` | Install auto-approval pattern. Flags: `--label-includes L1,L2`, `--tag NAME`, `--ttl-hours N` |
| `/override request <KIND> <target> [--justification "..."]` | Request operator override of a denial (the §10.11 ApprovalQueue path) |
| `/override list` / `/override show <id>` | Inspect override grants |

### Declassification
| Command | Purpose |
|---|---|
| `/schemas` | List available quarantined-extract schemas |
| `/extract <msg> <schema>` | Run quarantined-LLM extraction on an inbox message |

### Trace + tools
| Command | Purpose |
|---|---|
| `/trace` | Re-render the last turn's tool outcomes |
| `/audit [N] [--full]` | Show the last N audit events for the current session |
| `/tools [filter]` | List tools the daemon currently exposes, grouped by capability kind. Optional substring filter. |

### Clipboard
| Command | Purpose |
|---|---|
| `/copy recovery` | Copy the most recent recovery-step sequence (from a denied action) |
| `/copy approval <id>` | Copy verbatim approval payload |
| `/copy last` | Copy last agent response |
| `/copy trace <turn>` | Copy a turn's full audit trace as JSON |
| `/copy <literal>` | Copy arbitrary text |

On modern terminals (OSC 52 support), `/copy` lands directly in the system clipboard. On terminals without OSC 52, falls back to `~/.capdep/clipboard/<ts>-<sub>.txt`.

### Misc
| Command | Purpose |
|---|---|
| `/help` | Show the slash command reference |
| `/quit` / `/exit` / `/bye` | Exit the chat (bare `exit` / `quit` / `bye` also work) |

---

## Key bindings

| Binding | Action |
|---|---|
| `Enter` | Submit message |
| `Alt-Enter` | Insert newline (multi-line input) |
| `↑` / `↓` | History navigation |
| `Tab` | Autocomplete slash commands and contextual args |
| `Ctrl-C` | Interrupt current line |
| `Ctrl-D` | Exit chat |

---

## OSC 8 hyperlinks (modern terminals only)

On Ghostty / kitty / iTerm2 / WezTerm / Alacritty / modern xterm, certain rendered text becomes clickable:

- **Recovery commands** (rendered after a denied action) — click to indicate "paste this command"
- **Session IDs** in `/sessions` listings — click to insert `/switch <id>`
- **Approval IDs** in `/approvals` listings — click to insert `/approve <id>`
- **Tool names** in `/tools` listings — click to filter to that tool

Hyperlinks use the `capdep://paste/<urlencoded-command>` URI scheme. Terminal behavior on click varies — most show the URI in a tooltip / let you copy it via right-click; future work will add proper paste-on-click via terminal integration.

Terminals without OSC 8 see plain text — no broken sequences.

---

## Recovery synthesis (the IFC story made livable)

Every denied tool call now carries **literal pasteable slash commands** that would unblock the action. The agent quotes them verbatim; the REPL renders them inline.

Example flow:

```
chat> forward the hotel reservation email to marc@joneslaw.io
agent: I'll do that...
   ✗ deny  imap.send  rule=untrusted-meets-egress
   ↳ recover:
      /spawn "forward hotel reservation to marc@joneslaw.io"
                                                · Session is tainted by prior reads of
                                                  untrusted content; clean session has no
                                                  labels to conflict.
      /grant SEND_EMAIL marc@joneslaw.io --one-shot
                                                · Grant the capability in the fresh session.
      /override request SEND_EMAIL marc@joneslaw.io --justification "explicit user authorization"
                                                · Alternative: request operator override to
                                                  bypass the label conflict in this session.
```

Three deterministic recovery paths. Pick the simplest (the first), or use `/override` if you don't want to lose the session's context.

The agent's system prompt instructs it to **quote these literally** — never invent slash commands like `/capdep override request` (which doesn't exist).

---

## Default auto-grant capabilities

New sessions get these by default (`--no-default-caps` opts out):

| Cap | Scope |
|---|---|
| `READ_FS` | `~/Documents/*`, `~/Projects/*`, `~/Downloads/*`, `~/Desktop/*`, `/tmp/*` |
| `CALENDAR_READ` | `*` |
| `WEB_FETCH` | `*` |
| `CREATE_FS` | `~/.capdep/work/*`, `/tmp/*` |
| `EXECUTE_SANDBOX` | `scratch` region |

Anything outside requires explicit `/grant`. Recovery synthesis tells you exactly which command.

---

## Daemon reliability

- `daemon stop` reliably terminates via pidfile + signal escalation. No more orphaned daemons after backgrounded starts.
- Daemon version-mismatch warning fires on chat startup when the running daemon was started with code that differs from current source. Restart with `capdep daemon stop && capdep chat` to pick up changes.

---

## Pre-approval patterns

Use `/remember` to declare auto-approval rules for repeat workflows:

```
/remember SEND_EMAIL marc@joneslaw.io \
  --label-includes confidential.personal \
  --tag self-forward \
  --ttl-hours 168
```

This auto-approves any `SEND_EMAIL` to `marc@joneslaw.io` whose session has `confidential.personal` in its accumulated labels, for the next 168 hours. The `--tag` surfaces in audit so you can find these decisions later.

Without `--label-includes`, the pattern fires unconditionally — useful for low-stakes self-forwarding workflows.

---

## What's still in progress

Tracked on the [v0.5 — UX EPIC milestone](https://github.com/marctjones/capabledeputy/milestone/1):

- `--mode rich` is a scaffold — the existing `tui/console.py` Textual app. Most slash commands work only in line mode for now (Phase C work)
- Tabbed split-pane viewer (#17) — the side pane in rich mode is just the existing status sidebar; auto-opening tool output to a viewer is Phase C work
- Token streaming agent output (#16 track 2) — designed; deferred until Phase C lands
- Sixel / kitty graphics for sandbox-produced images (#19) — designed; needs a workflow
- `capdep tui` / `capdep console` removal — happens when the rich surface reaches feature parity

---

## Reference

- Architecture spec: [`specs/007-surface-convergence/spec.md`](../specs/007-surface-convergence/spec.md)
- Issue tracker: <https://github.com/marctjones/capabledeputy/issues>
- DESIGN.md (the deep architecture doc)
- Top-level CLAUDE.md / AGENTS.md (project conventions)
