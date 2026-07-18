"""Per-platform supervised-service unit generation (#318, per spike #314).

macOS launchd: `KeepAlive = { SuccessfulExit: false }` — restart on a crash
(non-zero exit) ONLY, never on a clean idle-exit — plus `ThrottleInterval` crash
throttling and `RunAtLoad`. This reconciles supervision with idle-shutdown
(#314); the supervised daily-driver runs resident (`CAPDEP_IDLE_SHUTDOWN=off`).

Linux systemd: `Restart=on-failure` + `RestartSec` + `StartLimit*` bounded crash
loop. Both generators are pure functions of their inputs — the CLI writes the
result and (best-effort) loads it.
"""

from __future__ import annotations

import sys
from pathlib import Path
from xml.sax.saxutils import escape

DEFAULT_LABEL = "local.capabledeputy.daemon"

# The daily-driver runs supervised + resident: the OS restarts on crash, so the
# daemon must not idle-exit (which would need socket-activation to be resumed).
_RESIDENT_ENV = {"CAPDEP_IDLE_SHUTDOWN_SECONDS": "off"}


def daemon_program_args(python: str | None = None) -> list[str]:
    """The argv that starts the daemon (matches the CLI autostart path)."""
    return [python or sys.executable, "-m", "capabledeputy.cli.main", "daemon", "start"]


def launchd_plist_path(label: str = DEFAULT_LABEL, *, home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "LaunchAgents" / f"{label}.plist"


def systemd_unit_path(name: str = "capabledeputy", *, home: Path | None = None) -> Path:
    return (home or Path.home()) / ".config" / "systemd" / "user" / f"{name}.service"


def launchd_plist(
    *,
    label: str = DEFAULT_LABEL,
    program_args: list[str] | None = None,
    env: dict[str, str] | None = None,
    throttle_interval: int = 10,
    stdout_log: Path | None = None,
    stderr_log: Path | None = None,
) -> str:
    """A launchd LaunchAgent plist: RunAtLoad + KeepAlive={SuccessfulExit:false}
    (restart on crash only) + ThrottleInterval crash throttling."""
    args = program_args or daemon_program_args()
    merged_env = {**_RESIDENT_ENV, **(env or {})}
    arg_xml = "\n".join(f"    <string>{escape(a)}</string>" for a in args)
    env_xml = "\n".join(
        f"    <key>{escape(k)}</key><string>{escape(v)}</string>" for k, v in merged_env.items()
    )
    logs = ""
    if stdout_log is not None:
        logs += f"  <key>StandardOutPath</key><string>{escape(str(stdout_log))}</string>\n"
    if stderr_log is not None:
        logs += f"  <key>StandardErrorPath</key><string>{escape(str(stderr_log))}</string>\n"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{escape(label)}</string>
  <key>ProgramArguments</key>
  <array>
{arg_xml}
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key><false/>
  </dict>
  <key>ThrottleInterval</key><integer>{int(throttle_interval)}</integer>
  <key>EnvironmentVariables</key>
  <dict>
{env_xml}
  </dict>
{logs}</dict>
</plist>
"""


def systemd_unit(
    *,
    exec_start: str | None = None,
    working_dir: Path | None = None,
    env: dict[str, str] | None = None,
    restart_sec: int = 10,
    start_limit_interval: int = 60,
    start_limit_burst: int = 5,
) -> str:
    """A systemd user unit: Restart=on-failure + RestartSec + a bounded
    StartLimit crash loop (after `burst` crashes in `interval`s, enter failed)."""
    exec_line = exec_start or " ".join(daemon_program_args())
    merged_env = {**_RESIDENT_ENV, **(env or {})}
    env_lines = "\n".join(f"Environment={k}={v}" for k, v in merged_env.items())
    wd = f"WorkingDirectory={working_dir}\n" if working_dir is not None else ""
    return f"""[Unit]
Description=CapableDeputy security runtime for personal AI agents
StartLimitIntervalSec={int(start_limit_interval)}
StartLimitBurst={int(start_limit_burst)}

[Service]
Type=simple
ExecStart={exec_line}
{wd}{env_lines}
Restart=on-failure
RestartSec={int(restart_sec)}

[Install]
WantedBy=default.target
"""
