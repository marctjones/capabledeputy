"""Unit tests for PodmanDevbox (persistent-container substrate).

Mocks `subprocess.run` + `subprocess.Popen` so tests exercise the
lifecycle (argv composition, idempotent start, exec, stop,
stop_session) without depending on a real `podman` binary.

The disposable PodmanSandboxActuator is covered by
test_podman_sandbox.py — this file is the persistent counterpart.
"""

from __future__ import annotations

import io
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import pytest

from capabledeputy.substrate.podman_devbox import (
    PodmanDevbox,
    PodmanStartError,
    _default_workspace_root,
)
from capabledeputy.substrate.podman_sandbox import (
    PodmanNotAvailable,
    PodmanRegionSpec,
    UnknownRegion,
)

# --- FakePopen for `podman exec` ----------------------------------------


class FakePopen:
    """Minimal subprocess.Popen stand-in for the `podman exec` path.

    Records argv, emits scripted stdout/stderr + exit code.
    """

    seen_argvs: list[list[str]] = []  # noqa: RUF012 — mutable on purpose; reset() between tests

    def __init__(self, argv, stdin=None, stdout=None, stderr=None, bufsize=0, **kw) -> None:
        FakePopen.seen_argvs.append(list(argv))
        self.argv = argv
        self.stdin = io.BytesIO() if stdin == subprocess.PIPE else None
        self.stdout = io.BytesIO(self.scripted_stdout)
        self.stderr = io.BytesIO(self.scripted_stderr)
        self._exit_code = self.scripted_exit_code
        self._killed = False
        self._delay = self.scripted_delay
        self._start = time.monotonic()

    # knobs
    scripted_stdout: bytes = b"ok\n"
    scripted_stderr: bytes = b""
    scripted_exit_code: int = 0
    scripted_delay: float = 0.0

    @classmethod
    def reset(cls) -> None:
        cls.seen_argvs = []
        cls.scripted_stdout = b"ok\n"
        cls.scripted_stderr = b""
        cls.scripted_exit_code = 0
        cls.scripted_delay = 0.0

    def poll(self):
        if self._killed:
            return 137
        if time.monotonic() - self._start < self._delay:
            return None
        return self._exit_code

    def wait(self, timeout=None):
        return self.poll()

    def terminate(self) -> None:
        self._killed = True

    def kill(self) -> None:
        self._killed = True


# --- subprocess.run patch ------------------------------------------------


class FakeRun:
    """Records every `subprocess.run` call so tests can assert that
    `podman run -d ...`, `podman inspect`, `podman kill`, `podman rm`
    were dispatched with the expected argv.

    Default response: returncode=0, empty output. Tests can override
    `responses` to drive specific outcomes (e.g. inspect returns
    "true" for alive, "false" for dead)."""

    calls: list[list[str]] = []  # noqa: RUF012 — mutable on purpose; reset() between tests
    responses: list[tuple[int, bytes, bytes]] = []  # noqa: RUF012 — same as above
    inspect_alive: bool = True  # default: containers report as running

    @classmethod
    def reset(cls) -> None:
        cls.calls = []
        cls.responses = []
        cls.inspect_alive = True

    @classmethod
    def __call__(cls, argv, **kwargs):
        cls.calls.append(list(argv))
        # Special-case podman version check
        if len(argv) >= 2 and argv[1] == "--version":
            return subprocess.CompletedProcess(argv, 0, b"podman v0\n", b"")
        # Special-case `podman inspect` for liveness
        if len(argv) >= 2 and argv[1] == "inspect":
            stdout = b"true" if cls.inspect_alive else b"false"
            return subprocess.CompletedProcess(argv, 0, stdout, b"")
        # Queued response — only consumed by `podman run` calls so
        # kill/rm housekeeping in start_or_get doesn't eat the test's
        # scripted response for the actual `run -d`.
        if cls.responses and len(argv) >= 2 and argv[1] == "run":
            rc, out, err = cls.responses.pop(0)
            return subprocess.CompletedProcess(argv, rc, out, err)
        return subprocess.CompletedProcess(argv, 0, b"", b"")


@pytest.fixture(autouse=True)
def _reset_fakes():
    FakePopen.reset()
    FakeRun.reset()
    yield
    FakePopen.reset()
    FakeRun.reset()


@pytest.fixture
def fake_podman(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", FakeRun())
    monkeypatch.setattr(subprocess, "Popen", FakePopen)


@pytest.fixture
def devbox(tmp_path: Path, fake_podman: None) -> PodmanDevbox:
    """A PodmanDevbox with a single `dev` spec and a per-test
    workspace root, using the FakeRun/FakePopen subprocess shims."""
    return PodmanDevbox(
        (
            PodmanRegionSpec(
                spec_id="dev",
                image="docker.io/library/python:3.14-slim",
                network="bridge",  # devbox often wants network for pip/npm
                memory_mb=512,
                cpus=1.0,
            ),
        ),
        workspace_root=tmp_path / "devbox-state",
        podman_bin="/fake/podman",
    )


# --- Construction --------------------------------------------------------


def test_construct_without_podman_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def always_missing(argv, **kw):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", always_missing)
    with pytest.raises(PodmanNotAvailable):
        PodmanDevbox(
            (PodmanRegionSpec(spec_id="dev", image="alpine"),),
            podman_bin="/nonexistent/podman",
        )


def test_construct_duplicate_spec_rejected(fake_podman: None) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        PodmanDevbox(
            (
                PodmanRegionSpec(spec_id="dev", image="alpine"),
                PodmanRegionSpec(spec_id="dev", image="alpine"),
            ),
            podman_bin="/fake/podman",
        )


def test_default_workspace_root_under_xdg_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """XDG_STATE_HOME wins when set; falls back to ~/.local/state
    otherwise. Tests both paths via env manipulation."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    assert _default_workspace_root() == tmp_path / "xdg-state" / "capdep" / "devbox"
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "homedir")
    assert (
        _default_workspace_root() == tmp_path / "homedir" / ".local" / "state" / "capdep" / "devbox"
    )


# --- Start lifecycle -----------------------------------------------------


def test_start_unknown_spec(devbox: PodmanDevbox) -> None:
    with pytest.raises(UnknownRegion):
        devbox.start_or_get(uuid4(), "not-a-spec")


def test_start_dispatches_podman_run_detached(devbox: PodmanDevbox) -> None:
    sid = uuid4()
    live = devbox.start_or_get(sid, "dev")
    # One of the FakeRun.calls must be the `podman run -d ... sleep infinity`
    run_calls = [c for c in FakeRun.calls if len(c) >= 2 and c[1] == "run"]
    assert len(run_calls) == 1
    cmd = run_calls[0]
    assert "-d" in cmd
    assert "--cap-drop=ALL" in cmd
    assert "--network" in cmd
    # Container is named with both session id + spec id so `podman ps`
    # output is recognizable.
    assert "--name" in cmd
    name_idx = cmd.index("--name")
    assert str(sid) in cmd[name_idx + 1]
    assert "dev" in cmd[name_idx + 1]
    # Keep-alive process tail
    assert cmd[-2:] == ["sleep", "infinity"]
    # Image (penultimate after stripping `sleep infinity`) is the spec image
    assert cmd[-3] == "docker.io/library/python:3.14-slim"
    # Workspace mount points at /work with `:U` so the unprivileged
    # in-container uid owns it.
    v_flag_idxs = [i for i, t in enumerate(cmd) if t == "-v"]
    assert any(":/work:rw" in cmd[i + 1] for i in v_flag_idxs)
    # The workspace dir was created on the host.
    assert live.workspace_host_path.is_dir()
    assert live.workspace_host_path.name == "work"


def test_start_is_idempotent_when_alive(devbox: PodmanDevbox) -> None:
    """Two starts of the same (session, spec) only invoke one `podman
    run`. The second call observes inspect=true and returns the
    cached live record."""
    sid = uuid4()
    a = devbox.start_or_get(sid, "dev")
    FakeRun.inspect_alive = True
    b = devbox.start_or_get(sid, "dev")
    assert a.container_name == b.container_name
    run_calls = [c for c in FakeRun.calls if len(c) >= 2 and c[1] == "run"]
    assert len(run_calls) == 1


def test_start_restarts_when_container_dead(devbox: PodmanDevbox) -> None:
    """If inspect reports the container is not running, start_or_get
    reaps the stale record and spins up a fresh container."""
    sid = uuid4()
    devbox.start_or_get(sid, "dev")
    FakeRun.inspect_alive = False
    devbox.start_or_get(sid, "dev")
    run_calls = [c for c in FakeRun.calls if len(c) >= 2 and c[1] == "run"]
    assert len(run_calls) == 2


def test_start_fails_loudly_on_nonzero_podman_run(devbox: PodmanDevbox) -> None:
    """Queue a non-zero response for the next `podman run`. The
    daemon must surface the failure rather than silently caching a
    dead handle."""
    # Skip the version check that already happened during construction;
    # queue one failure for the `podman run -d`.
    FakeRun.responses = [(125, b"", b"image not found")]
    with pytest.raises(PodmanStartError, match="image not found"):
        devbox.start_or_get(uuid4(), "dev")


# --- Exec ----------------------------------------------------------------


def test_exec_auto_starts_then_dispatches_exec(devbox: PodmanDevbox) -> None:
    sid = uuid4()
    FakePopen.scripted_stdout = b"hello from devbox\n"
    FakePopen.scripted_exit_code = 0
    result = devbox.exec(sid, "dev", argv=("echo", "hi"), timeout_seconds=5)
    # podman run -d for the start, then podman exec for the run
    run_calls = [c for c in FakeRun.calls if len(c) >= 2 and c[1] == "run"]
    assert len(run_calls) == 1
    assert len(FakePopen.seen_argvs) == 1
    exec_argv = FakePopen.seen_argvs[0]
    assert exec_argv[0] == "/fake/podman"
    assert exec_argv[1] == "exec"
    assert "-w" in exec_argv  # workdir flag
    assert exec_argv[exec_argv.index("-w") + 1] == "/work"
    assert exec_argv[-2:] == ["echo", "hi"]
    assert result.exit_code == 0
    assert result.stdout == b"hello from devbox\n"
    assert not result.cancelled
    assert not result.timed_out


def test_exec_passes_stdin_bytes(devbox: PodmanDevbox) -> None:
    sid = uuid4()
    result = devbox.exec(
        sid,
        "dev",
        argv=("cat",),
        timeout_seconds=5,
        stdin_bytes=b"piped input",
    )
    assert result.exit_code == 0


def test_exec_timeout_kills_only_exec_not_container(
    devbox: PodmanDevbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exec process is killed on timeout, but the keep-alive
    `sleep infinity` keeps the container up — so a subsequent exec
    should NOT trigger a re-start."""
    sid = uuid4()
    FakePopen.scripted_delay = 5.0  # never finishes in our 1s budget
    result = devbox.exec(sid, "dev", argv=("sleep", "999"), timeout_seconds=1)
    assert result.timed_out
    # One start, one exec — no extra start during teardown
    run_calls = [c for c in FakeRun.calls if len(c) >= 2 and c[1] == "run"]
    assert len(run_calls) == 1


# --- Stop / list ---------------------------------------------------------


def test_stop_returns_false_when_nothing_to_stop(devbox: PodmanDevbox) -> None:
    assert devbox.stop(uuid4(), "dev") is False


def test_stop_tears_down_live_container(devbox: PodmanDevbox) -> None:
    sid = uuid4()
    devbox.start_or_get(sid, "dev")
    assert devbox.stop(sid, "dev") is True
    # rm -f was called on the container name
    rm_calls = [c for c in FakeRun.calls if len(c) >= 2 and c[1] == "rm"]
    assert any("-f" in c for c in rm_calls)


def test_stop_preserves_workspace_by_default(devbox: PodmanDevbox) -> None:
    sid = uuid4()
    live = devbox.start_or_get(sid, "dev")
    # Seed a file inside the workspace.
    (live.workspace_host_path / "scratch.txt").write_text("kept across restarts")
    devbox.stop(sid, "dev")
    assert (live.workspace_host_path / "scratch.txt").exists()


def test_stop_purge_workspace_removes_dir(devbox: PodmanDevbox) -> None:
    sid = uuid4()
    live = devbox.start_or_get(sid, "dev")
    (live.workspace_host_path / "scratch.txt").write_text("about to be nuked")
    devbox.stop(sid, "dev", purge_workspace=True)
    assert not live.workspace_host_path.exists()


def test_stop_session_tears_down_every_devbox_for_session(
    tmp_path: Path,
    fake_podman: None,
) -> None:
    db = PodmanDevbox(
        (
            PodmanRegionSpec(spec_id="py", image="python:slim"),
            PodmanRegionSpec(spec_id="node", image="node:20"),
        ),
        workspace_root=tmp_path / "ws",
        podman_bin="/fake/podman",
    )
    sid_a = uuid4()
    sid_b = uuid4()
    db.start_or_get(sid_a, "py")
    db.start_or_get(sid_a, "node")
    db.start_or_get(sid_b, "py")
    # End session A only — both A's containers should be torn down,
    # B's should stay alive.
    assert db.stop_session(sid_a) == 2
    assert db.list_session(sid_a) == ()
    remaining = db.list_session(sid_b)
    assert len(remaining) == 1
    assert remaining[0]["spec_id"] == "py"


def test_list_session_returns_workspace_path(devbox: PodmanDevbox) -> None:
    sid = uuid4()
    devbox.start_or_get(sid, "dev")
    entries = devbox.list_session(sid)
    assert len(entries) == 1
    e = entries[0]
    assert e["spec_id"] == "dev"
    # Host workspace path ends in `/<spec>/work` and is under the
    # test's tmp workspace root — i.e. the entry surfaces a real
    # host path, not the container-side `/work`.
    workspace_path = e["workspace_host_path"]
    assert isinstance(workspace_path, str)
    assert workspace_path.endswith("/dev/work")
    assert "devbox-state" in workspace_path
    assert str(sid) in workspace_path
    assert e["is_alive"] is True


def test_list_specs_returns_all_declared(devbox: PodmanDevbox) -> None:
    assert devbox.list_specs() == ("dev",)


# --- reap_idle -----------------------------------------------------------


def test_reap_idle_skips_recent(devbox: PodmanDevbox) -> None:
    """A container started/exec'd within the threshold is NOT
    reaped. start_or_get sets last_exec_at, so even a never-exec'd
    container counts as fresh."""
    sid = uuid4()
    devbox.start_or_get(sid, "dev")
    reaped = devbox.reap_idle(idle_seconds=3600)
    assert reaped == []
    assert len(devbox.list_session(sid)) == 1


def test_reap_idle_kills_stale(devbox: PodmanDevbox) -> None:
    """A container whose last_exec_at is older than the threshold
    gets `podman rm -f`'d. The injectable `now` lets us simulate
    time passing without sleeping."""
    sid = uuid4()
    live = devbox.start_or_get(sid, "dev")
    # Pretend the container was last touched two hours ago.
    live.last_exec_at -= 7200
    reaped = devbox.reap_idle(idle_seconds=3600)
    assert reaped == [(sid, "dev")]
    assert devbox.list_session(sid) == ()


def test_reap_idle_uses_injected_clock(devbox: PodmanDevbox) -> None:
    """The `now` arg lets the reaper run against a deterministic
    timestamp rather than wall-clock — important so tests don't
    flake on slow machines."""
    sid = uuid4()
    live = devbox.start_or_get(sid, "dev")
    # `now` two hours after start → effective idle = 7200
    future = live.last_exec_at + 7200
    reaped = devbox.reap_idle(idle_seconds=3600, now=future)
    assert reaped == [(sid, "dev")]


def test_reap_idle_preserves_workspace(devbox: PodmanDevbox) -> None:
    """A reaped container's workspace is intentionally kept so the
    operator (or a later restart of the same session) can recover
    work. Use `capdep maintenance workspaces --apply` to free that
    space deliberately."""
    sid = uuid4()
    live = devbox.start_or_get(sid, "dev")
    (live.workspace_host_path / "build.log").write_text("don't delete me")
    live.last_exec_at -= 7200
    devbox.reap_idle(idle_seconds=3600)
    assert (live.workspace_host_path / "build.log").exists()


def test_reap_idle_resets_on_exec(devbox: PodmanDevbox) -> None:
    """exec() bumps last_exec_at, so a long-running build that
    finishes 50 minutes in still gives the operator their full
    idle window from finish time. Without this fix, `last_exec_at`
    could lag and the reaper could kill an active workspace."""
    sid = uuid4()
    live = devbox.start_or_get(sid, "dev")
    # Push start back 50 minutes
    live.last_exec_at -= 3000
    # exec() resets last_exec_at to "now". After exec returns, the
    # idle window starts over.
    devbox.exec(sid, "dev", argv=("true",), timeout_seconds=5)
    # 30 minutes idle from now — should NOT reap (threshold 1 hour)
    reaped = devbox.reap_idle(
        idle_seconds=3600,
        now=devbox._live[(sid, "dev")].last_exec_at + 1800,
    )
    assert reaped == []
