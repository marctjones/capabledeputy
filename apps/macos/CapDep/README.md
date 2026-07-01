# CapDep macOS Shell

Native macOS supervisory shell for the CapableDeputy daemon.

This app is intentionally a **client**. It does not make policy decisions and
does not run tools directly. It talks to the Python daemon over the existing
newline-delimited JSON-RPC Unix socket and renders daemon state:

- daemon connectivity
- active sessions
- pending approvals
- recent audit events
- approval approve/deny actions

## Run from source

Run the app:

```bash
cd apps/macos/CapDep
./scripts/run-local-app.sh
```

Use the script rather than `swift run CapDepMac` for interactive desktop
testing. SwiftPM's raw executable can run the process without giving macOS a
normal `.app` bundle, which may leave no visible desktop window.

The app tries to connect to an existing daemon first. If the socket is
unreachable, it asks the existing CLI lifecycle path to stop any stale daemon
recorded by the pidfile, then starts a fresh daemon and polls until `ping`
succeeds. Set `CAPDEP_GUI_DAEMON_COMMAND` to a shell command prefix to override
the lifecycle command; by default the app uses the repo-local `.venv/bin/capdep`
when present, otherwise `capdep` from `PATH`.

For local source launches, `scripts/run-local-app.sh` owns daemon startup and
exports `CAPDEP_GUI_OWNS_DAEMON=0` into the app bundle. This avoids macOS
GUI-process permission failures when Python tries to inspect the repo `.venv`.
The default local daemon mode is a dedicated tmux server via
`scripts/run-local-daemon-tmux.sh`, which keeps stdout/stderr visible while
surviving the app launch. The script verifies daemon parity before opening the
app and then verifies again that the opened `CapDepMac` process is still
connected. A local launch is not considered successful until that post-open
check prints `[capdep-gui] app connected to daemon`.

Useful daemon helpers:

```bash
scripts/run-local-daemon-tmux.sh restart
scripts/run-local-daemon-tmux.sh logs
scripts/run-local-daemon-tmux.sh stop
```

`CAPDEP_DAEMON_MODE=launchd` is also available through
`scripts/run-local-daemon-launchd.sh` for normal per-user LaunchAgent testing.
When launched from sandboxed automation, launchd can inherit the automation
sandbox and fail to read the repo venv; use the tmux helper in that case.

Only one `CapDepMac` instance may run at a time. A second launch exits before
opening another menu bar item or touching daemon lifecycle state.

When launched with `swift run`, the executable is not inside a `.app` bundle, so
macOS notification permission setup is skipped. Packaged app builds still use
UserNotifications for pending approval alerts.

The socket path follows the daemon convention:

1. `CAPDEP_SOCKET`
2. `$XDG_RUNTIME_DIR/capdep.sock`
3. `/tmp/capdep-$UID.sock`

The daemon also shuts down automatically after it has no connected clients for
`CAPDEP_IDLE_SHUTDOWN_SECONDS` seconds. The default is 60 seconds; set the env
var to `0` or `off` to keep the daemon resident.

## Gmail MCP OAuth Setup

Open `Settings > Accounts > Google Gmail MCP` to configure the official Gmail
remote MCP server. The Swift app sends the OAuth client ID and secret to the
daemon over the local Unix socket; the daemon writes:

- `~/.config/capabledeputy/servers.d/google-gmail.yaml`
- `~/.config/capabledeputy/oauth/google-gmail-client-id`
- `~/.config/capabledeputy/oauth/google-gmail-client-secret`
- `~/.config/capabledeputy/oauth/google-gmail.json` after browser authorization

The client secret and token files are mode `0600`. Restart the daemon after the
OAuth token is created so the newly configured Gmail MCP server is loaded into
the tool registry.

## Product Boundary

The Swift app is the native shell for macOS integration. The Python daemon
remains the trusted policy chokepoint. Future native work should preserve this
boundary: menu bar, command palette, notifications, Touch ID, Finder/Share
extensions, and Shortcuts actions may relay explicit user intent, but they must
not soften or bypass daemon policy.

Keep reusable behavior daemon-first:

- Add new workflow primitives as daemon RPCs/events before adding Swift-only
  behavior.
- Keep approval semantics, labels, provenance, trust mutations, tool dispatch,
  and setup checks in the daemon.
- Let the Swift app focus on rendering, platform integration, accessibility,
  notifications, and operator input.
- Maintain parity with CLI/TUI surfaces wherever a workflow is not inherently
  macOS-specific.

## Admin MCP Setup Surface

External MCP hosts can configure local connector setup through:

```bash
capdep mcp-admin-server
```

This is separate from `capdep mcp-server --session-id ...`. The session-bound
server exposes normal policy-gated tools. The admin server exposes local setup
operations such as Gmail OAuth status, OAuth client configuration, and browser
authorization through daemon RPCs.
