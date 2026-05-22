# Spec 007 — Surface Convergence + Rich Terminal UX

**Status:** Draft RFC
**Tracks:** Issue [#15](https://github.com/marctjones/capabledeputy/issues/15) (this spec) + [#16](https://github.com/marctjones/capabledeputy/issues/16) (REPL feature parity) + [#17](https://github.com/marctjones/capabledeputy/issues/17) (split-pane viewer) + [#18](https://github.com/marctjones/capabledeputy/issues/18) (OSC 8) + [#19](https://github.com/marctjones/capabledeputy/issues/19) (sixel/kitty graphics) + [#20](https://github.com/marctjones/capabledeputy/issues/20) (OSC 52 — done)
**Milestone:** [v0.5 — UX EPIC](https://github.com/marctjones/capabledeputy/milestone/1)
**Author:** designed during the May 2026 UX EPIC sprint
**Status of foundations:** terminal capability detection module (`cli/terminal_caps.py`) landed at commit `1af39f9` — this spec depends on it.

---

## The problem

Capdep ships three overlapping interactive surfaces:

| Entry | LOC | Role |
|---|---|---|
| `capdep chat` (`cli/chat.py`) | 1700+ | Line-oriented REPL — primary, every new feature lands here |
| `capdep tui` (`tui/app.py`) | 540 | Read-only spectator (sessions/approvals/conversation/trace panes) |
| `capdep console` (`tui/console.py`) | 202 | Textual-based REPL with sidebar — half-finished convergence attempt |

Drift between them is real maintenance cost: every UX feature (recovery synthesis #3, inline approval #7, `/override` #4, split-pane viewer #17, OSC 8 #18) would otherwise have to be rebuilt three times.

## The proposed model

**One entry point that adapts to terminal capability.** `capdep chat` detects the terminal at startup and picks a rendering layer:

- **Modern terminal** (Ghostty, kitty, iTerm2, WezTerm, Alacritty — detected by `terminal_caps.caps()`) → Textual-based rich surface: side panel with live label/cap state, tabbed content viewer (#17), OSC 8 hyperlinks (#18), mouse selection, bracketed-paste multi-line input, optional inline graphics (#19).
- **Basic terminal** (`TERM=dumb`, ssh-without-tty, plain xterm) → current prompt-toolkit line-oriented REPL with Rich formatting where supported, plain text where not.

Same daemon RPC backend. Same slash-command vocabulary. Same approval flow. Only the rendering layer differs.

`capdep tui` becomes a Ctrl+L "spectate-only" mode of the same surface. `capdep console` is removed; its `tui/console.py` was the half-built convergence attempt — its useful logic (sidebar + RichLog) folds into the new rich surface.

## The rendering abstraction

The key design decision: what's the API surface between feature code and the renderer?

### Option A — single `Renderer` protocol

```python
class Renderer(Protocol):
    def print(self, content: RenderableContent) -> None: ...
    def prompt(self, ...) -> str: ...
    def show_modal(self, payload: ModalPayload) -> ModalDecision: ...
    def toggle_viewer(self, mode: ViewerMode) -> None: ...
    def push_to_viewer(self, tab: ViewerTab, content: Any) -> None: ...
    def stream(self, source: AsyncIterator[str]) -> None: ...
```

Feature code (slash handlers, agent turn rendering, approval review) calls `Renderer` methods. Two implementations:

- `LineRenderer` — backed by `prompt-toolkit` + Rich. Modal becomes inline prompt. Viewer becomes pager (`bat`/`less`).
- `RichRenderer` — backed by Textual. Modal becomes a screen. Viewer becomes the tabbed side pane (#17).

**Trade-off:** clean abstraction, but every feature touch needs both implementations. Large initial port.

### Option B — Textual everywhere, with a "line-mode skin"

Textual already runs on basic terminals via its `TERM=dumb`-aware fallback. Make the rich surface the always-on, and configure it to degrade visually on basic terminals (no Unicode borders, no colors, no mouse — but the same Textual app underneath).

**Trade-off:** simpler — one implementation. But Textual's basic-terminal mode is rougher than a polished prompt-toolkit REPL; users on `ssh -t` to a server might prefer the line-mode UX.

### Option C — Hybrid: rich-only for modern, keep line-mode REPL as separate surface

Textual app for modern terminals. Existing `chat.py` REPL stays as the fallback. They share daemon-side logic + the `terminal_caps` module + the recovery-synthesis / approval-queue infrastructure but have separate UI code.

**Trade-off:** less abstraction work; two UI codebases stay in sync via discipline + the shared backend.

### Recommendation: Option C (hybrid), with strict discipline

Reasons:
1. Feature work to date (recovery synthesis #3, inline approval #7, `/override` #4, /copy #20) all lands in shared backend (RPC + presentation helpers like `_render_recovery_steps`). The UI layer is thin.
2. Textual's basic-terminal mode is genuinely rough — operators on `ssh -t` deserve the line-mode polish.
3. Option A's Renderer protocol is over-engineering for a 2-surface project. Two codebases that stay in sync via tests is honest.
4. The terminal_caps module already gives us the discriminator. `capdep chat` checks `caps().family` at startup and dispatches.

## Entry-point dispatch

```python
@app.command("chat")
def chat_command(... existing args ...) -> None:
    from capabledeputy.cli.terminal_caps import caps

    c = caps()
    if c.family in ("dumb", "unknown", "vscode", "xterm") or not c.is_tty:
        # Line-mode REPL (current chat.py path)
        return _line_mode_chat(... existing args ...)
    # Modern terminal — rich Textual surface
    return _rich_mode_chat(... existing args ...)
```

Operators who want to force a specific mode get `--mode line | rich | auto` (default `auto`).

## The rich surface — layout

```
┌─ chat ────────────────────────────────────────┬─ viewer ──────────────────┐
│                                               │ [output][file][trace][?]  │
│ user: list my unread emails                   │ ────────────────────────  │
│                                               │  (output rendered here    │
│ agent: Here are your unread emails:           │   when significant tool   │
│ ...                                           │   results return — see    │
│   ✓ gmail.users.messages.list                 │   the auto-open heuristic │
│                                               │   in #17)                 │
│ chat> _                                       │                           │
└─ session: abc12345 · labels: confidential.* ──┴───────────────────────────┘
```

Bindings (per [#17](https://github.com/marctjones/capabledeputy/issues/17)):
- `Ctrl+E` cycles viewer modes (hidden / side / focused)
- `Ctrl+L` opens viewer with most-recent significant output
- `Tab` / `Shift+Tab` switches focus between chat and viewer
- `Esc` closes the viewer

Side-pane behaviors are inherited from issue #17's design.

## Migration & deprecation path

1. **Phase A — foundation (done)**: `terminal_caps` module (1af39f9), recovery synthesis (c37ab71), inline approval (1497561), OSC 52 (4550865). All work for both surfaces because the rendering is in shared helpers.

2. **Phase B — rich surface scaffold (next session)**: Build `_rich_mode_chat()` as a Textual app that calls the same daemon RPCs `chat.py` calls. Initially renders the conversation + input box without the side panel — feature parity with line mode is the bar.

3. **Phase C — split-pane viewer (per #17)**: Add the tabbed viewer pane to the rich surface only. Auto-open heuristic for significant tool output. `/view` / `/pin` / `/close` slash commands work in both surfaces; in line mode they shell to `bat` / `less`.

4. **Phase D — modern-terminal enhancements**: OSC 8 audit hyperlinks (#18), sixel/kitty graphics (#19) — all gated on `terminal_caps.caps()` flags.

5. **Phase E — deprecate `capdep tui` / `capdep console`**: Print a deprecation warning + redirect to `capdep chat`. After two releases, remove the modules.

## Specific design choices baked in this spec

- **Family-based dispatch, not feature-based.** We don't probe individual features (does this terminal support OSC 8? does it support sixel?). We name families and look up the supported feature set. Simpler, less prone to detection bugs.

- **Line mode stays first-class.** Not a degraded experience — a deliberate polish. Operators on ssh, in CI, on minimal terminals deserve the line-mode REPL kept maintained.

- **No Renderer abstraction.** Two UI codebases (line + rich), shared backend, discipline + tests keep them in sync. Don't over-engineer.

- **Slash-command vocabulary is shared.** `/grant`, `/override`, `/copy`, etc. — same names in both surfaces, same RPCs, same arg shapes. Recovery synthesis (#3) emits the same commands; both surfaces render them.

- **Auto-open heuristic for the viewer is configurable.** Operators who want the viewer always-hidden until explicit `/view` should have that option.

- **Modal vs inline approval.** Line mode keeps inline (per #7's current implementation). Rich mode can use a Textual `ModalScreen` for the verbatim payload, but defaults to an inline panel so operators don't lose the conversation context.

## Open questions for review

1. **Force-mode flag**: `--mode auto | line | rich`. Reasonable? Or should auto-detection always win and operators rely on `TERM=dumb` to force line mode?

2. **Spectate mode**: Today `capdep tui` is read-only. Folding it into `Ctrl+L` on the rich surface means the read-only mode shares state with the active chat session. Is that the right semantics, or do operators sometimes want a truly separate spectator process?

3. **Multi-session navigation**: The line-mode REPL has `/switch <id>` for moving between sessions. Rich mode could have a session-tree sidebar showing parent/child relationships. Is that worth building?

4. **Telemetry**: should the daemon log which surface (line / rich) a session is being driven from? Useful for understanding feature usage; potentially privacy-sensitive.

## Out of scope for this spec

- Full Textual app implementation (Phase B work, not this RFC)
- Sixel / kitty graphics protocol details (#19's spec when that work lands)
- Pre-existing `capdep tui` / `capdep console` removal (Phase E)

## Acceptance criteria for spec sign-off

- [ ] Design decisions above reviewed and approved (or counterproposals accepted)
- [ ] Phase B implementation issue carved out from #15 with concrete acceptance criteria
- [ ] Open questions answered or deferred to implementation discretion
- [ ] Deprecation timeline for `capdep tui` / `capdep console` agreed
