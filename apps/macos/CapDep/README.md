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
swift run CapDepMac
```

The app tries to connect to an existing daemon first. If the socket is
unreachable, it asks the existing CLI lifecycle path to stop any stale daemon
recorded by the pidfile, then starts a fresh daemon and polls until `ping`
succeeds. Set `CAPDEP_GUI_DAEMON_COMMAND` to a shell command prefix to override
the lifecycle command; by default the app uses the repo-local `.venv/bin/capdep`
when present, otherwise `capdep` from `PATH`.

The socket path follows the daemon convention:

1. `CAPDEP_SOCKET`
2. `$XDG_RUNTIME_DIR/capdep.sock`
3. `/tmp/capdep-$UID.sock`

The daemon also shuts down automatically after it has no connected clients for
`CAPDEP_IDLE_SHUTDOWN_SECONDS` seconds. The default is 60 seconds; set the env
var to `0` or `off` to keep the daemon resident.

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
