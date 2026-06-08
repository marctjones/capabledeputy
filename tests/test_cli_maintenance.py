"""Unit tests for `capdep maintenance` CLI.

The CLI talks to `podman` (via subprocess) and the local daemon (via
DaemonClient). Tests mock both so they don't need either present.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from capabledeputy.cli import maintenance as maint
from capabledeputy.cli.maintenance import maintenance_app

runner = CliRunner()


@pytest.fixture
def fake_podman(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Replace subprocess.run with a recorder. Returns the list of
    argvs the CLI invoked; tests can also seed `podman_ps_lines` to
    drive what `podman ps` returns."""
    invocations: list[list[str]] = []

    class FakeRun:
        def __init__(self):
            self.podman_ps_lines: list[bytes] = []

        def __call__(self, argv, **kwargs):  # type: ignore[misc]
            invocations.append(list(argv))
            if "ps" in argv:
                stdout = b"\n".join(self.podman_ps_lines) + b"\n" if self.podman_ps_lines else b""
                return subprocess.CompletedProcess(argv, 0, stdout, b"")
            return subprocess.CompletedProcess(argv, 0, b"", b"")

    fake_run = FakeRun()
    monkeypatch.setattr(subprocess, "run", fake_run)
    return invocations


@pytest.fixture
def fake_workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point `_default_workspace_root` at a tmp dir so tests can
    stage sessions/specs/files without touching XDG state."""
    root = tmp_path / "devbox"
    root.mkdir()
    monkeypatch.setattr(maint, "_default_workspace_root", lambda: root)
    return root


@pytest.fixture
def daemon_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the daemon isn't running so `_live_session_ids`
    returns an empty set. The workspaces command should treat every
    on-disk session as orphan in that case."""
    monkeypatch.setattr(maint, "_live_session_ids", lambda: set())


# --- status --------------------------------------------------------------


def test_status_shows_zero_containers_when_podman_empty(
    fake_podman: list[list[str]],
    fake_workspace_root: Path,
    daemon_offline: None,
) -> None:
    result = runner.invoke(maintenance_app, ["status"])
    assert result.exit_code == 0
    assert "containers: 0 total" in result.stdout
    assert "no devbox state dir" in result.stdout or "0 session dir(s)" in result.stdout


def test_status_shows_containers_and_workspaces(
    fake_podman: list[list[str]],
    fake_workspace_root: Path,
    daemon_offline: None,
) -> None:
    fake_podman_lines = [
        b"capdep-devbox-aaa-py\tUp 5 minutes\tpython:slim\t2026-06-03",
        b"capdep-devbox-bbb-node\tExited (0) 10 minutes\tnode:20\t2026-06-02",
    ]
    # Re-seed the recorder's ps response.
    subprocess.run.podman_ps_lines = fake_podman_lines  # type: ignore[attr-defined]
    # Stage a workspace dir
    sid = "11111111-1111-1111-1111-111111111111"
    work_dir = fake_workspace_root / sid / "py" / "work"
    work_dir.mkdir(parents=True)
    (work_dir / "f").write_text("x")

    result = runner.invoke(maintenance_app, ["status"])
    assert result.exit_code == 0
    assert "containers: 2 total" in result.stdout
    assert "1 running" in result.stdout
    assert "1 stopped" in result.stdout
    assert "1 session dir(s)" in result.stdout


# --- containers ----------------------------------------------------------


def test_containers_dry_run_lists_but_does_not_remove(
    fake_podman: list[list[str]],
) -> None:
    subprocess.run.podman_ps_lines = [  # type: ignore[attr-defined]
        b"capdep-devbox-aaa-py\tUp 5 minutes\tpython:slim\t2026-06-03",
        b"capdep-devbox-bbb-node\tExited (0) 10 minutes\tnode:20\t2026-06-02",
    ]
    result = runner.invoke(maintenance_app, ["containers"])
    assert result.exit_code == 0
    assert "capdep-devbox-aaa-py" in result.stdout
    assert "capdep-devbox-bbb-node" in result.stdout
    # 1 stopped container ⇒ should mention --apply but NOT have invoked rm
    assert "--apply" in result.stdout
    rm_calls = [c for c in fake_podman if len(c) >= 2 and c[1] == "rm"]
    assert rm_calls == []


def test_containers_apply_removes_stopped_only(
    fake_podman: list[list[str]],
) -> None:
    subprocess.run.podman_ps_lines = [  # type: ignore[attr-defined]
        b"capdep-devbox-aaa-py\tUp 5 minutes\tpython:slim\t2026-06-03",
        b"capdep-devbox-bbb-node\tExited (0) 10 minutes\tnode:20\t2026-06-02",
        b"capdep-devbox-ccc-rust\tExited (137) 1 hour\trust:slim\t2026-06-02",
    ]
    result = runner.invoke(maintenance_app, ["containers", "--apply"])
    assert result.exit_code == 0
    assert "removed 2/2" in result.stdout
    rm_argvs = [c for c in fake_podman if len(c) >= 2 and c[1] == "rm"]
    rm_targets = {c[-1] for c in rm_argvs}
    assert rm_targets == {
        "capdep-devbox-bbb-node",
        "capdep-devbox-ccc-rust",
    }
    # The running one MUST stay
    assert "capdep-devbox-aaa-py" not in rm_targets


# --- workspaces ----------------------------------------------------------


def test_workspaces_empty_state_dir_short_circuits(
    fake_podman: list[list[str]],
    fake_workspace_root: Path,
    daemon_offline: None,
) -> None:
    result = runner.invoke(maintenance_app, ["workspaces"])
    assert result.exit_code == 0
    assert "empty" in result.stdout or "no devbox state dir" in result.stdout


def test_workspaces_dry_run_lists_orphans_without_deleting(
    fake_podman: list[list[str]],
    fake_workspace_root: Path,
    daemon_offline: None,
) -> None:
    sid = "11111111-1111-1111-1111-111111111111"
    work = fake_workspace_root / sid / "py" / "work"
    work.mkdir(parents=True)
    (work / "build.log").write_text("hello")
    result = runner.invoke(maintenance_app, ["workspaces"])
    assert result.exit_code == 0
    # daemon offline → session is "unknown" → orphan
    assert "orphan" in result.stdout
    assert "--apply" in result.stdout
    # Workspace must still exist after dry-run
    assert (work / "build.log").exists()


def test_workspaces_apply_deletes_orphan_dirs(
    fake_podman: list[list[str]],
    fake_workspace_root: Path,
    daemon_offline: None,
) -> None:
    sid = "22222222-2222-2222-2222-222222222222"
    work = fake_workspace_root / sid / "node" / "work"
    work.mkdir(parents=True)
    (work / "app.js").write_text("console.log('orphan')")
    result = runner.invoke(maintenance_app, ["workspaces", "--apply"])
    assert result.exit_code == 0
    assert "removed 1/1" in result.stdout
    assert not (fake_workspace_root / sid).exists()


def test_workspaces_keeps_live_session_dirs(
    fake_podman: list[list[str]],
    fake_workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the daemon reports this session as alive, the dir is
    NOT considered orphan even with --apply. Safety guarantee
    against deleting in-flight work."""
    sid = "33333333-3333-3333-3333-333333333333"
    work = fake_workspace_root / sid / "py" / "work"
    work.mkdir(parents=True)
    (work / "main.py").write_text("# don't delete")
    monkeypatch.setattr(maint, "_live_session_ids", lambda: {sid})
    result = runner.invoke(maintenance_app, ["workspaces", "--apply"])
    assert result.exit_code == 0
    assert "no orphan workspace dirs" in result.stdout
    assert (work / "main.py").exists()


def test_workspaces_keeps_dir_with_running_container_even_if_session_unknown(
    fake_podman: list[list[str]],
    fake_workspace_root: Path,
    daemon_offline: None,
) -> None:
    """Defense in depth: a container running with this session id
    in its name keeps the workspace safe even when the daemon is
    down (so it doesn't think the session exists)."""
    sid = "44444444-4444-4444-4444-444444444444"
    work = fake_workspace_root / sid / "py" / "work"
    work.mkdir(parents=True)
    (work / "in_progress.txt").write_text("active build")
    subprocess.run.podman_ps_lines = [  # type: ignore[attr-defined]
        f"capdep-devbox-{sid}-py\tUp 2 minutes\tpython:slim\t2026-06-03".encode(),
    ]
    result = runner.invoke(maintenance_app, ["workspaces", "--apply"])
    assert result.exit_code == 0
    assert (work / "in_progress.txt").exists()
    assert "container live, session unknown" in result.stdout
