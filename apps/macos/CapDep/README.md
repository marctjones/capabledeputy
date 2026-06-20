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

Start the daemon first:

```bash
capdep daemon start --config configs/personal-assistant/daemon.yaml
```

Then run the app:

```bash
cd apps/macos/CapDep
swift run CapDepMac
```

The socket path follows the daemon convention:

1. `CAPDEP_SOCKET`
2. `$XDG_RUNTIME_DIR/capdep.sock`
3. `/tmp/capdep-$UID.sock`

## Product Boundary

The Swift app is the native shell for macOS integration. The Python daemon
remains the trusted policy chokepoint. Future native work should preserve this
boundary: menu bar, command palette, notifications, Touch ID, Finder/Share
extensions, and Shortcuts actions may relay explicit user intent, but they must
not soften or bypass daemon policy.
