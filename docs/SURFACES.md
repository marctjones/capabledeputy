# Which Surface Do I Use?

CapableDeputy is a daemon with several clients. They all talk to the
**same daemon over the same JSON-RPC socket** — state is shared; pick
by what you're trying to do, not by capability.

## Start here

```bash
uv run capdep daemon start            # the brain — start this first
uv run capdep daemon start -v         # …with live RPC logging
```

Everything else is a client of that daemon. With no daemon running,
every client tells you to start it.

## The decision

| I want to… | Use | Why |
|---|---|---|
| **Try it fast, guided** | `capdep demo start <name>` | Seeds a scenario + drops you into the REPL. Best first run. `capdep demo list` to see scenarios. |
| **Talk to the agent (terminal/SSH/scriptable)** | `capdep chat <session>` | Linear REPL: type → agent acts → policy trace. Full slash-command set (`/spawn`, `/grant`, `/extract`, `/approve`, …). Copy-pasteable transcript. |
| **Talk to the agent *and* watch the security model live, one window** | `capdep console <session>` | Input + live compartment/capability sidebar + verbatim approval modal. The "see the cage" surface. Needs a TTY. |
| **Watch what's happening without driving** | `capdep tui` | Read-only spectator: sessions, approvals, trace, event ticker, session-graph view. Good for a second screen / demos to an audience. |
| **Just approve/deny pending requests** | `capdep approval list` / `approve <id>` / `deny <id>` | Scriptable control-plane. The TUIs do this interactively with a verbatim modal. |
| **Inspect/triage after the fact** | `capdep session list` · `capdep audit …` · `capdep trace <session>` · `capdep policy …` · `capdep tool …` | Read-only introspection. |
| **Run a programmatic-mode plan** | `capdep run <session> <prog.py>` (`--bundle` for one-approval workflows) | LLM emits a Python plan; statically dry-run-able (`capdep dry-run`). |
| **Drive from an external agent/host (MCP)** | `capdep mcp-server --session-id <id>` | Exposes session-bound daemon tools to an MCP client. |
| **Configure local connectors from an MCP host** | `capdep mcp-admin-server` | Exposes local setup/admin tools such as Gmail OAuth configuration. |

## Three ways to "talk to the agent" — the real difference

| | drives agent | live security view | approve in-place | form factor |
|---|---|---|---|---|
| `capdep chat` | ✅ | inline trace + bottom toolbar | inline prompt | linear, scrollback, SSH-friendly |
| `capdep console` | ✅ | ✅ live sidebar | ✅ verbatim modal | full-screen TUI, needs a TTY |
| `capdep tui` | ❌ | ✅ panes | ✅ verbatim modal | full-screen spectator |

Rule of thumb: **`chat`** to get work done in a terminal; **`console`**
to feel the compartment fill / demo the security story; **`tui`** as a
passive monitor on a second screen. They share the daemon, so you can
run `chat` in one terminal and `tui` in another against the same
session.

## The non-negotiable invariant (applies to every surface)

Enforcement is the daemon's deterministic `decide()` at the single
dispatch chokepoint — **never the LLM, never the client**. No surface
can approve, unblock, or soften policy; clients only *render* what the
daemon decided and relay the human's explicit approvals. Slash
commands and approvals are user-driven daemon passthroughs; the agent
never sees them. See [DESIGN.md](../DESIGN.md) and the project
constitution (`.specify/memory/constitution.md`, Principles I & V).

Feature work follows the same rule. If a workflow or safety behavior should
exist in the macOS GUI, it should first exist as daemon RPC/state/event
contract so the CLI, TUI, Swift GUI, and future Windows/Linux GUIs can reach
feature parity without reimplementing enforcement.

## Recovering from a block

A `DENY` is structural, not "ask nicely." Match the recovery to the
rule (the agent will also suggest these):

- `untrusted/health/financial → egress` → `/spawn` a clean session,
  or `/extract` a declassified fact first
- `capability-expired` → `/grant` again (optionally longer `--ttl`)
- `rate-limit-exceeded` → wait for the window, or `/grant --rate`
  higher
- `capability-revoked-by-prior-use` → `/spawn` (fresh session hasn't
  used the revoking tool)

Full matrix: [demos/18-recoverable-blocks.md](demos/18-recoverable-blocks.md).
