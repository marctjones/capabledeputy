# Spike #314 — Daemon supervision mechanism (cross-platform)

**Status:** resolved · **Date:** 2026-07-18 · **Blocks:** v0.57 (#318)
**Question:** pick the supervised auto-restart mechanism per platform (macOS
launchd, Linux systemd/quadlet), decide whether an in-process watchdog is
needed, and settle the idle-shutdown interaction.

## TL;DR decision

- **macOS:** launchd with **`KeepAlive = { SuccessfulExit: false }`** (restart on
  crash / non-zero exit only, never on a clean stop) + **`ThrottleInterval` = 10s**
  crash throttling + `RunAtLoad = true`. Resident daily-driver ⇒ idle-shutdown
  **off**.
- **Linux:** the existing **systemd-quadlet** (`deploy/capabledeputy.container`,
  rootless, isolated) with explicit **`Restart=on-failure`**, `RestartSec=10`,
  and start-limit throttling; a plain user `.service` (same directives) for
  non-container installs.
- **In-process watchdog for the daemon: NO.** The OS supervisor is the
  process-restart authority; a watchdog *inside* the process it watches can't
  restart it after a hard crash. (The in-process supervisor we already have —
  `upstream/supervisor.py` `LiveSession` — supervises the *MCP subprocesses*, a
  different job, and stays.)
- **Idle-shutdown:** two deployment modes; never combine plain `KeepAlive=true`
  with idle-shutdown on (restart-thrash). See the table.

## Current state (evidence)

- **launchd** (`scripts/run-local-daemon-launchd.sh`): `RunAtLoad=true`,
  **`KeepAlive=false`**, and it forces **`CAPDEP_IDLE_SHUTDOWN_SECONDS='off'`** —
  i.e. resident but **unsupervised**: a crash leaves the daemon dead until a
  manual reload.
- **Linux** (`deploy/capabledeputy.container`): a systemd-quadlet unit exists
  (rootless, volume-mounted, env-file'd) but the file does not state an explicit
  `Restart=` policy — it relies on the quadlet default.
- **On-demand autostart** (`cli/chat.py` `_ensure_daemon`): `capdep chat
  --autostart` `Popen`s the daemon `start_new_session=True` and polls the socket.
  One-shot, **no supervision** — the code comments call the resulting
  daemon-not-running surface "confusing" (the #319 reconnect concern).
- **Idle-shutdown** (`daemon/lifecycle.py`): default **60s** with no clients;
  `CAPDEP_IDLE_SHUTDOWN_SECONDS=0/off/false` keeps it resident. Exists so
  one-shot polling clients don't leave a daemon running forever.

## The idle-shutdown × KeepAlive tension (the core of the spike)

Plain launchd `KeepAlive=true` restarts the process on **any** exit — including a
**clean idle-shutdown** — producing a restart every 60s (thrash). That is exactly
why the current plist disables idle-shutdown. The reconciliation is launchd's
conditional form:

- **`KeepAlive = { SuccessfulExit: false }`** — restart only when the process
  exits **non-zero** (a crash). A clean idle-exit (exit 0) is **not** restarted.

Two coherent deployment modes fall out of that:

| Mode | Who launches | idle-shutdown | Supervisor | Notes |
|---|---|---|---|---|
| **On-demand (dev / light)** | client `--autostart` | **on** (60s) | none | self-cleaning; a crash just means the next `--autostart` respawns it |
| **Supervised resident (daily driver)** | launchd / systemd | **off** | OS, restart-on-crash | never idle-exits; the OS restarts it on crash. **Recommended default for #318.** |
| Supervised + on-demand (advanced) | launchd **socket activation** | on | OS, restart-on-crash | launchd owns the socket, launches the daemon on first connect, idle-shutdown reaps it; `KeepAlive={SuccessfulExit:false}` handles crashes. Elegant but requires passing the launchd socket fd into the daemon — a **follow-up**, not #318. |

**Recommendation:** ship the **supervised-resident** mode as the daily-driver
default (idle-shutdown off, `KeepAlive={SuccessfulExit:false}`). Keep the
on-demand autostart mode for dev. Defer socket-activation as an optimization.

## Crash throttling (avoid a tight restart loop)

- **launchd:** set **`ThrottleInterval`** (seconds; default 10) as the minimum
  between respawns. launchd has no "give up after N" — a permanently-crashing
  daemon respawns every `ThrottleInterval` forever, which is acceptable (the log
  shows the crash loop; `capdep doctor` reports it). Log loudly on repeated
  early exits.
- **systemd:** `Restart=on-failure`, `RestartSec=10`, and
  **`StartLimitIntervalSec=60` + `StartLimitBurst=5`** → after 5 crashes in 60s
  systemd enters `failed` and stops trying (with `systemctl reset-failed` to
  recover). Prefer this bounded behavior; surface it in `capdep doctor`.

## Per-platform artifacts to ship in #318

**macOS launchd plist** (installed by `capdep-setup` / a new `capdep service
install`):
```xml
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
<key>ThrottleInterval</key><integer>10</integer>
<key>EnvironmentVariables</key><dict>
  <key>CAPDEP_IDLE_SHUTDOWN_SECONDS</key><string>off</string>
</dict>
```
(Replaces the current `KeepAlive=false`; keeps `IDLE_SHUTDOWN=off`.)

**Linux** — amend `deploy/capabledeputy.container`:
```ini
[Service]
Restart=on-failure
RestartSec=10
StartLimitIntervalSec=60
StartLimitBurst=5
[Container]
Environment=CAPDEP_IDLE_SHUTDOWN_SECONDS=off
```
(Quadlets accept a `[Service]` section that passes through to the generated
unit.) Ship an equivalent plain `~/.config/systemd/user/capabledeputy.service`
for non-container installs.

## In-process watchdog: why NOT

A watchdog thread inside the daemon can restart *sub*-components (it already does
— `LiveSession` respawns dead MCP subprocesses) but **cannot** recover the daemon
from a hard crash, OOM-kill, or panic — the watchdog dies with it. Process-level
restart must come from **outside** the process (launchd/systemd). Adding a
second, redundant in-process daemon-watchdog would give false confidence.
Verdict: rely on the OS supervisor; keep the existing subprocess supervisor.

## Mapping to #318

1. Flip the launchd plist to `KeepAlive={SuccessfulExit:false}` + `ThrottleInterval`.
2. Add explicit `Restart=on-failure` + start-limit throttling to the quadlet +
   a plain systemd user unit.
3. Add a `capdep service install/uninstall/status` command that installs the
   right per-platform unit and reports supervision state (feeds `capdep doctor`).
4. Keep on-demand `--autostart` (idle-shutdown on) as the dev mode; the daily
   driver uses the supervised unit (idle-shutdown off).
5. Do **not** add an in-process daemon watchdog.

Deferred follow-up: launchd/systemd **socket activation** for a supervised +
idle-reaped hybrid.
