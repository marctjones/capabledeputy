"""#318 — supervised-service unit generators are pure and encode the spike-#314
supervision contract (launchd KeepAlive={SuccessfulExit:false}, systemd
Restart=on-failure), including the resident idle-shutdown-off env."""

from __future__ import annotations

from pathlib import Path

from capabledeputy.service import (
    DEFAULT_LABEL,
    daemon_program_args,
    launchd_plist,
    launchd_plist_path,
    systemd_unit,
    systemd_unit_path,
)


def test_daemon_program_args_starts_the_daemon() -> None:
    args = daemon_program_args("/usr/bin/python3")
    assert args == ["/usr/bin/python3", "-m", "capabledeputy.cli.main", "daemon", "start"]


def test_supervised_command_is_the_foreground_blocking_entrypoint() -> None:
    """A supervisor (launchd/systemd Type=simple) tracks the ExecStart PID: the
    command it launches MUST block in the foreground. If `daemon_program_args`
    is ever repointed at a detaching/background command, launchd sees a clean
    exit (no restart under SuccessfulExit:false) and systemd marks the service
    stopped — supervision silently no-ops. Pin the invariant here so a rename
    can't reintroduce a detach. `daemon start` calls anyio.run(run_daemon) and
    blocks until shutdown (main.py: 'Start the daemon in the foreground')."""
    import inspect

    from capabledeputy.cli.main import daemon_start

    assert daemon_program_args()[-2:] == ["daemon", "start"]
    # The wired subcommand must run the daemon in-process (blocking), not spawn
    # and return. `run_daemon` is the long-lived serve coroutine.
    src = inspect.getsource(daemon_start)
    assert "run_daemon" in src
    assert "Popen" not in src and "start_new_session" not in src


def test_launchd_plist_supervision_contract() -> None:
    text = launchd_plist()
    # Restart on crash ONLY — never on a clean (idle) exit.
    assert "<key>KeepAlive</key>" in text
    assert "<key>SuccessfulExit</key><false/>" in text
    # Start at load + throttle a crash loop.
    assert "<key>RunAtLoad</key><true/>" in text
    assert "<key>ThrottleInterval</key><integer>10</integer>" in text
    # Supervised daily-driver runs resident: idle-shutdown must be off.
    assert "CAPDEP_IDLE_SHUTDOWN_SECONDS" in text
    assert "<string>off</string>" in text
    assert f"<string>{DEFAULT_LABEL}</string>" in text


def test_launchd_plist_is_well_formed_xml() -> None:
    import xml.etree.ElementTree as ET

    ET.fromstring(launchd_plist())  # raises on malformed XML


def test_launchd_plist_escapes_untrusted_values() -> None:
    text = launchd_plist(env={"X": "a & b <c>"})
    assert "a &amp; b &lt;c&gt;" in text
    assert "a & b <c>" not in text


def test_launchd_plist_respects_overrides() -> None:
    text = launchd_plist(
        label="custom.label",
        throttle_interval=42,
        stdout_log=Path("/tmp/out.log"),
        stderr_log=Path("/tmp/err.log"),
    )
    assert "<string>custom.label</string>" in text
    assert "<key>ThrottleInterval</key><integer>42</integer>" in text
    assert "<key>StandardOutPath</key><string>/tmp/out.log</string>" in text
    assert "<key>StandardErrorPath</key><string>/tmp/err.log</string>" in text


def test_systemd_unit_supervision_contract() -> None:
    text = systemd_unit()
    assert "Restart=on-failure" in text
    assert "RestartSec=10" in text
    assert "StartLimitIntervalSec=60" in text
    assert "StartLimitBurst=5" in text
    # Resident: idle-shutdown off.
    assert "Environment=CAPDEP_IDLE_SHUTDOWN_SECONDS=off" in text
    assert "WantedBy=default.target" in text


def test_systemd_unit_respects_overrides() -> None:
    text = systemd_unit(
        exec_start="/opt/capdep run",
        working_dir=Path("/srv/capdep"),
        restart_sec=3,
        start_limit_burst=9,
    )
    assert "ExecStart=/opt/capdep run" in text
    assert "WorkingDirectory=/srv/capdep" in text
    assert "RestartSec=3" in text
    assert "StartLimitBurst=9" in text


def test_unit_paths_are_under_home() -> None:
    home = Path("/home/tester")
    plist = launchd_plist_path(home=home)
    assert plist == home / "Library" / "LaunchAgents" / f"{DEFAULT_LABEL}.plist"
    unit = systemd_unit_path(home=home)
    assert unit == home / ".config" / "systemd" / "user" / "capabledeputy.service"
