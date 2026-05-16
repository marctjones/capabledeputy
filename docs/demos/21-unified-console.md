# Demo 21: Unified Console — Drive + Monitor + Approve

**Audience:** anyone who wants the whole loop in one window.
**Time:** ~5 minutes.
**Requires:** an Anthropic API key (it drives the real agent).

`capdep console <session-id>` is one window that does all three jobs
the prior surfaces split across two terminals:

- **Drive** — an input box: type to the agent, the conversation +
  per-tool policy trace stream into the main pane.
- **Monitor** — a live sidebar: compartment health (color-graded,
  flips to **TAINTED** the moment the agent reads untrusted content)
  and every capability with its full v0.7 constraint set
  (one-shot / destructive / max / expiry / rate / revoked).
- **Approve** — when the runtime queues an approval (server-side, at
  the policy chokepoint), the **verbatim-payload modal pops up
  immediately** for review; `a` approves, `d` denies, `esc` defers.

Contrast with the two existing surfaces:

| Surface | Drive | Monitor | Approve |
|---|---|---|---|
| `capdep chat` (REPL) | ✅ | inline trace | inline prompt |
| `capdep tui` (spectator) | ❌ | ✅ panes | ✅ modal |
| **`capdep console`** | ✅ | ✅ live sidebar | ✅ modal |

## Run it

```bash
# terminal 1
export ANTHROPIC_API_KEY=$(cat CLAUDEAPI.KEY | tr -d '[:space:]')
uv run capdep daemon start

# terminal 2 — seed a session, note its id
uv run capdep demo start daily-briefing --no-chat
uv run capdep console <session-id-from-above>
```

(`demo start … --no-chat` seeds the scenario and prints the session
id without entering the REPL, so you can hand it to the console.)

## What to try

1. **Watch the compartment turn red.** Ask: *"summarise my unread
   email."* The sidebar's compartment flips `clean → TAINTED` as
   `inbox.list` adds `untrusted.external` — the security model made
   visible, live, without reading scrollback.
2. **See a block + its recovery.** Ask it to forward that email
   externally. The trace shows `✗ deny rule=untrusted-meets-egress`
   and the same deterministic `↳ recover:` hint the REPL gives.
3. **Approve in the modal.** In a scenario that queues an approval
   (e.g. a financial purchase), the verbatim-payload modal opens the
   instant it's queued — review byte-for-byte, press `a`. The
   dispatch (purpose-limited session) result is logged inline.

Keys: `ctrl+a` re-open the latest pending approval, `ctrl+q` quit,
`/quit` in the input also exits.

## Architecture / honesty

- The Textual shell is thin. Every formatting and selection decision
  lives in `tui/console_model.py`, **unit-tested** (6 tests) — the
  project's Textual apps have no integration tests by long-standing
  precedent, so the logic that can break is tested at the model
  layer, not the widget.
- The compartment / capability rendering and the deny→recovery map
  come from the shared `capabledeputy.presentation` module — the same
  single source of truth the REPL and spectator TUI use, so all three
  surfaces read the security model identically.
- Enforcement is untouched: the console only calls `session.send` and
  `approval.{approve,deny}`. Policy still decides server-side at the
  one chokepoint; approvals are still registered there, LLM-isolated.
- `capdep tui` (spectator, with the session-graph view) is kept — the
  console is additive, not a replacement.

Known scope: session-control slash commands (`/spawn`, `/grant`,
`/extract`, …) remain REPL-only for now; the console's input is
agent-message + `/quit`. Folding those in is a clean follow-up.

## Files

- `src/capabledeputy/tui/console.py` — the Textual app (thin shell)
- `src/capabledeputy/tui/console_model.py` — tested pure view-model
- `src/capabledeputy/presentation.py` — shared security-render SoT
- `src/capabledeputy/tui/app.py` — reused `ApprovalDetailScreen`
- `tests/test_tui_console_model.py`, `tests/test_presentation.py`
