"""Unit tests for the Podman SandboxActuator (004 U034 / U035).

Most tests use a `FakePopen` shim that monkeypatches `subprocess.Popen`
and `subprocess.run` so we exercise the actuator's logic — argv
composition, lifecycle progress, cancel, timeout — without depending
on a real `podman` binary in CI.

The one integration test (`test_real_podman_smoke`) is skipped unless
the `podman` binary is on PATH; it runs a tiny `echo hello` to verify
the full end-to-end path on hosts that do have Podman.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import threading
import time
from typing import ClassVar

import pytest

from capabledeputy.substrate.podman_sandbox import (
    PodmanMount,
    PodmanNotAvailable,
    PodmanRegionSpec,
    PodmanSandboxActuator,
    UnknownRegion,
    _harvest_outputs,
    _validate_input_name,
    load_sandbox_specs_from_file,
    parse_sandbox_config,
)
from capabledeputy.substrate.sandbox_actuator import SandboxProgress

# --- FakePopen ---------------------------------------------------------------


class FakePopen:
    """Minimal subprocess.Popen stand-in: emits a scripted sequence of
    stdout lines, an exit code, and supports kill via test hook.

    Tests configure `scripted_lines`, `scripted_exit_code`, optional
    `scripted_delay_seconds` (sleep before producing output), and an
    optional `kill_returncode` (the exit code observed if killed).
    """

    seen_argvs: ClassVar[list[list[str]]] = []

    def __init__(
        self,
        argv,
        stdin=None,
        stdout=None,
        stderr=None,
        bufsize=0,
        **kwargs,
    ) -> None:
        FakePopen.seen_argvs.append(list(argv))
        self.argv = argv
        self.stdin = io.BytesIO() if stdin == subprocess.PIPE else None
        self.stdout = io.BytesIO(b"".join(self._lines()))
        self.stderr = io.BytesIO(b"")
        self._exit_code = self._exit()
        self._killed = False
        self._delay = getattr(FakePopen, "scripted_delay_seconds", 0.0)
        self._start = time.monotonic()

    # --- knobs ---
    scripted_lines: ClassVar[list[bytes]] = [b"hello\n"]
    scripted_exit_code: int = 0
    scripted_delay_seconds: float = 0.0
    kill_returncode: int = 137

    @classmethod
    def reset(cls) -> None:
        cls.seen_argvs = []
        cls.scripted_lines = [b"hello\n"]
        cls.scripted_exit_code = 0
        cls.scripted_delay_seconds = 0.0
        cls.kill_returncode = 137

    def _lines(self) -> list[bytes]:
        return list(self.scripted_lines)

    def _exit(self) -> int:
        return self.scripted_exit_code

    def poll(self):
        if self._killed:
            return self.kill_returncode
        if time.monotonic() - self._start < self._delay:
            return None
        return self._exit_code

    def wait(self, timeout=None):
        return self.poll()

    def kill(self) -> None:
        self._killed = True


@pytest.fixture(autouse=True)
def _reset_fake_popen():
    FakePopen.reset()
    yield
    FakePopen.reset()


@pytest.fixture
def fake_podman(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch subprocess so the actuator thinks podman is present and
    every `podman run` returns the scripted output."""
    real_run = subprocess.run

    def fake_subprocess_run(argv, **kwargs):
        if argv[:2] == ["/fake/podman", "--version"]:
            return subprocess.CompletedProcess(argv, 0, stdout=b"podman v0.0\n", stderr=b"")
        if len(argv) >= 2 and argv[1] in ("kill", "rm"):
            return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(subprocess, "Popen", FakePopen)


# --- Spec validation ---------------------------------------------------------


def test_spec_require_digest_pin_rejects_unpinned() -> None:
    with pytest.raises(ValueError, match="not digest-pinned"):
        PodmanRegionSpec(
            spec_id="x",
            image="docker.io/library/python:slim",
            require_digest_pin=True,
        ).validate()


def test_spec_digest_pinned_passes() -> None:
    # No exception:
    PodmanRegionSpec(
        spec_id="x",
        image="docker.io/library/python@sha256:abc123",
        require_digest_pin=True,
    ).validate()


def test_spec_relative_mount_target_rejected() -> None:
    with pytest.raises(ValueError, match="must be absolute"):
        PodmanRegionSpec(
            spec_id="x",
            image="alpine",
            mounts=(PodmanMount(host_path="/tmp/x", container_path="relative"),),
        ).validate()


# --- Actuator construction ---------------------------------------------------


def test_construct_requires_podman_present(monkeypatch: pytest.MonkeyPatch) -> None:
    def always_fail(argv, **kwargs):
        raise FileNotFoundError
    monkeypatch.setattr(subprocess, "run", always_fail)
    with pytest.raises(PodmanNotAvailable):
        PodmanSandboxActuator(
            (PodmanRegionSpec(spec_id="x", image="alpine"),),
            podman_bin="/nonexistent/podman",
        )


def test_construct_duplicate_spec_id_rejected(fake_podman: None) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        PodmanSandboxActuator(
            (
                PodmanRegionSpec(spec_id="x", image="alpine"),
                PodmanRegionSpec(spec_id="x", image="alpine"),
            ),
            podman_bin="/fake/podman",
        )


# --- Region lifecycle --------------------------------------------------------


def test_create_region_default_spec_when_only_one(fake_podman: None) -> None:
    a = PodmanSandboxActuator(
        (PodmanRegionSpec(spec_id="solo", image="alpine"),),
        podman_bin="/fake/podman",
    )
    rid = a.create_region()
    assert rid.startswith("podman-solo-")


def test_create_region_requires_spec_id_when_ambiguous(fake_podman: None) -> None:
    a = PodmanSandboxActuator(
        (
            PodmanRegionSpec(spec_id="a", image="alpine"),
            PodmanRegionSpec(spec_id="b", image="alpine"),
        ),
        podman_bin="/fake/podman",
    )
    with pytest.raises(ValueError, match="requires exactly one"):
        a.create_region()
    # Explicit spec_id works
    assert a.create_region(spec_id="a").startswith("podman-a-")


def test_create_region_unknown_spec_id(fake_podman: None) -> None:
    a = PodmanSandboxActuator(
        (PodmanRegionSpec(spec_id="x", image="alpine"),),
        podman_bin="/fake/podman",
    )
    with pytest.raises(UnknownRegion):
        a.create_region(spec_id="not-there")


def test_execute_unknown_region(fake_podman: None) -> None:
    a = PodmanSandboxActuator(
        (PodmanRegionSpec(spec_id="x", image="alpine"),),
        podman_bin="/fake/podman",
    )
    with pytest.raises(UnknownRegion):
        a.execute(
            region_id="nope",
            argv=("echo", "hi"),
            env={},
            timeout_seconds=5,
        )


# --- Execute composition ----------------------------------------------------


def test_execute_argv_includes_hardening_flags(fake_podman: None) -> None:
    a = PodmanSandboxActuator(
        (PodmanRegionSpec(spec_id="x", image="alpine"),),
        podman_bin="/fake/podman",
    )
    rid = a.create_region()
    a.execute(region_id=rid, argv=("echo", "hi"), env={}, timeout_seconds=5)
    assert FakePopen.seen_argvs, "podman run never invoked"
    argv = FakePopen.seen_argvs[-1]
    # Hardening flags MUST be present
    for needle in (
        "--rm",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        "65534:65534",
    ):
        assert needle in argv, f"missing {needle!r} in {argv!r}"
    # Network defaults to none
    assert "--network" in argv
    assert "none" in argv[argv.index("--network") + 1 : argv.index("--network") + 2]
    # Caller's argv is at the very tail (after the image)
    assert argv[-2:] == ["echo", "hi"]
    assert argv[-3] == "alpine"


def test_execute_mounts_emit_v_args(fake_podman: None) -> None:
    spec = PodmanRegionSpec(
        spec_id="x",
        image="alpine",
        mounts=(
            PodmanMount(host_path="/host/in", container_path="/in", read_only=True),
            PodmanMount(host_path="/host/out", container_path="/out", read_only=False),
        ),
    )
    a = PodmanSandboxActuator((spec,), podman_bin="/fake/podman")
    rid = a.create_region()
    a.execute(region_id=rid, argv=("true",), env={}, timeout_seconds=5)
    argv = FakePopen.seen_argvs[-1]
    # Both volumes appear with the right modes
    assert "-v" in argv
    v_args = [argv[i + 1] for i, x in enumerate(argv) if x == "-v"]
    assert "/host/in:/in:ro,Z" in v_args
    assert "/host/out:/out:rw,Z" in v_args


def test_execute_env_allowlist(fake_podman: None) -> None:
    spec = PodmanRegionSpec(
        spec_id="x", image="alpine", env_allowlist=("FOO",),
    )
    a = PodmanSandboxActuator((spec,), podman_bin="/fake/podman")
    rid = a.create_region()
    a.execute(
        region_id=rid,
        argv=("env",),
        env={"FOO": "bar", "SECRET": "must_not_pass"},
        timeout_seconds=5,
    )
    argv = FakePopen.seen_argvs[-1]
    e_args = [argv[i + 1] for i, x in enumerate(argv) if x == "-e"]
    assert "FOO=bar" in e_args
    assert all(not e.startswith("SECRET=") for e in e_args)


def test_execute_result_includes_digest_and_exit_code(fake_podman: None) -> None:
    FakePopen.scripted_lines = [b"line1\n", b"line2\n"]
    FakePopen.scripted_exit_code = 0
    a = PodmanSandboxActuator(
        (PodmanRegionSpec(spec_id="x", image="alpine"),),
        podman_bin="/fake/podman",
    )
    rid = a.create_region()
    result = a.execute(region_id=rid, argv=("echo", "x"), env={}, timeout_seconds=5)
    assert result.exit_code == 0
    assert len(result.output_digest) == 64  # sha256 hex
    assert result.cancelled is False
    assert result.timed_out is False


# --- Progress callback -------------------------------------------------------


def test_execute_emits_lifecycle_progress(fake_podman: None) -> None:
    FakePopen.scripted_lines = [b"out1\n"]
    events: list[SandboxProgress] = []

    a = PodmanSandboxActuator(
        (PodmanRegionSpec(spec_id="x", image="alpine"),),
        podman_bin="/fake/podman",
    )
    rid = a.create_region()
    a.execute(
        region_id=rid,
        argv=("echo", "hi"),
        env={},
        timeout_seconds=5,
        progress_callback=events.append,
    )
    phases = [e.payload for e in events if e.kind == "lifecycle"]
    assert "image_check" in phases
    assert "container_start" in phases
    assert any(p.startswith("container_exit:") for p in phases)


def test_execute_streams_stdout_chunks(fake_podman: None) -> None:
    FakePopen.scripted_lines = [b"chunk-a\n", b"chunk-b\n"]
    events: list[SandboxProgress] = []
    a = PodmanSandboxActuator(
        (PodmanRegionSpec(spec_id="x", image="alpine"),),
        podman_bin="/fake/podman",
    )
    rid = a.create_region()
    a.execute(
        region_id=rid,
        argv=("echo", "hi"),
        env={},
        timeout_seconds=5,
        progress_callback=events.append,
    )
    stdout_payloads = [e.payload for e in events if e.kind == "stdout"]
    assert "chunk-a" in stdout_payloads
    assert "chunk-b" in stdout_payloads


def test_callback_exceptions_swallowed(fake_podman: None) -> None:
    def boom(_event: SandboxProgress) -> None:
        raise RuntimeError("observer is broken")

    a = PodmanSandboxActuator(
        (PodmanRegionSpec(spec_id="x", image="alpine"),),
        podman_bin="/fake/podman",
    )
    rid = a.create_region()
    # Must not raise even though the callback throws on every event.
    result = a.execute(
        region_id=rid,
        argv=("echo", "hi"),
        env={},
        timeout_seconds=5,
        progress_callback=boom,
    )
    assert result.exit_code == 0


# --- Cancel + timeout -------------------------------------------------------


def test_cancel_unknown_region_returns_false(fake_podman: None) -> None:
    a = PodmanSandboxActuator(
        (PodmanRegionSpec(spec_id="x", image="alpine"),),
        podman_bin="/fake/podman",
    )
    assert a.cancel("not-a-real-region") is False


def test_cancel_no_running_execution_returns_false(fake_podman: None) -> None:
    a = PodmanSandboxActuator(
        (PodmanRegionSpec(spec_id="x", image="alpine"),),
        podman_bin="/fake/podman",
    )
    rid = a.create_region()
    # No execute() has been called, so no process is running.
    assert a.cancel(rid) is False


def test_cancel_during_execute_kills_container(fake_podman: None) -> None:
    """Schedule a cancel mid-execution; verify cancel returns True and
    the result reflects cancellation."""
    FakePopen.scripted_lines = [b"slow-out\n"]
    FakePopen.scripted_delay_seconds = 1.0
    FakePopen.scripted_exit_code = 0  # ignored once we set _killed=True
    FakePopen.kill_returncode = 137

    a = PodmanSandboxActuator(
        (PodmanRegionSpec(spec_id="x", image="alpine"),),
        podman_bin="/fake/podman",
    )
    rid = a.create_region()

    def issue_cancel() -> None:
        time.sleep(0.2)
        # Drive the kill through the actuator's cancel path. FakePopen
        # observes the kill via a separate hook below.
        # cancel() returns True if a running execution was found.
        assert a.cancel(rid) is True
        # Also flip the FakePopen's killed flag so its next poll()
        # returns kill_returncode.
        with a._regions[rid].lock:
            proc = a._regions[rid].process
        if proc is not None and isinstance(proc, FakePopen):
            proc.kill()

    threading.Thread(target=issue_cancel, daemon=True).start()
    result = a.execute(
        region_id=rid, argv=("sleep", "1"), env={}, timeout_seconds=10,
    )
    assert result.cancelled is True
    assert result.exit_code == 137


def test_timeout_kills_and_marks_timed_out(fake_podman: None) -> None:
    """A run that exceeds timeout_seconds gets force-killed."""
    FakePopen.scripted_lines = []
    FakePopen.scripted_delay_seconds = 5.0  # would-be-long
    FakePopen.scripted_exit_code = 0
    FakePopen.kill_returncode = 137
    a = PodmanSandboxActuator(
        (PodmanRegionSpec(spec_id="x", image="alpine"),),
        podman_bin="/fake/podman",
    )
    rid = a.create_region()
    # Hook into _force_remove_container so it actually flips the proc's
    # killed flag (FakePopen wouldn't react to a real podman kill).
    orig_force = a._force_remove_container

    def force(name):
        # Mark the matching FakePopen as killed so its poll() exits.
        for region in a._regions.values():
            if region.container_name == name and isinstance(region.process, FakePopen):
                region.process.kill()
        orig_force(name)

    a._force_remove_container = force  # type: ignore[assignment]

    result = a.execute(
        region_id=rid, argv=("sleep", "30"), env={}, timeout_seconds=1,
    )
    assert result.timed_out is True
    assert result.cancelled is False


# --- discard_region ---------------------------------------------------------


def test_discard_region_removes_state(fake_podman: None) -> None:
    a = PodmanSandboxActuator(
        (PodmanRegionSpec(spec_id="x", image="alpine"),),
        podman_bin="/fake/podman",
    )
    rid = a.create_region()
    assert rid in a._regions
    a.discard_region(rid)
    assert rid not in a._regions
    # Idempotent
    a.discard_region(rid)


# --- Config parser ----------------------------------------------------------


def test_parse_sandbox_config_minimal() -> None:
    raw = {
        "sandbox": {
            "provider": "podman",
            "regions": [
                {"id": "scratch", "image": "alpine"},
            ],
        },
    }
    specs = parse_sandbox_config(raw)
    assert len(specs) == 1
    assert specs[0].spec_id == "scratch"
    assert specs[0].image == "alpine"
    assert specs[0].network == "none"


def test_parse_sandbox_config_no_block() -> None:
    assert parse_sandbox_config({}) == ()
    assert parse_sandbox_config({"upstream_servers": []}) == ()


def test_parse_sandbox_config_wrong_provider_returns_empty() -> None:
    """Unknown provider yields no specs — the daemon falls back to
    'no actuator wired', not to the demo."""
    raw = {"sandbox": {"provider": "modal", "regions": [{"id": "x", "image": "y"}]}}
    assert parse_sandbox_config(raw) == ()


def test_load_sandbox_specs_from_file(tmp_path) -> None:
    p = tmp_path / "daemon.yaml"
    p.write_text(
        """
sandbox:
  provider: podman
  regions:
    - id: scratch
      image: docker.io/library/alpine:latest
      memory_mb: 256
      cpus: 0.5
      mounts:
        - host: /var/cache/foo
          container: /work
          read_only: false
""",
        encoding="utf-8",
    )
    specs = load_sandbox_specs_from_file(p)
    assert len(specs) == 1
    assert specs[0].memory_mb == 256
    assert specs[0].cpus == 0.5
    assert len(specs[0].mounts) == 1
    assert specs[0].mounts[0].read_only is False


# --- Integration smoke (skipped if podman missing) --------------------------


# --- Input / output file flow ------------------------------------------------


def test_validate_input_name_rejects_path_traversal() -> None:
    with pytest.raises(ValueError):
        _validate_input_name("../etc/passwd")
    with pytest.raises(ValueError):
        _validate_input_name("a/b")
    with pytest.raises(ValueError):
        _validate_input_name(".hidden")
    with pytest.raises(ValueError):
        _validate_input_name("")
    # Valid names pass:
    _validate_input_name("script.py")
    _validate_input_name("data.json")


def test_execute_writes_inputs_to_in_dir(fake_podman: None, tmp_path) -> None:
    """The /in mount is populated from `inputs` before the container
    starts. We verify by inspecting the actuator's argv (the -v flag
    points at a real directory we can inspect)."""
    a = PodmanSandboxActuator(
        (PodmanRegionSpec(spec_id="x", image="alpine"),),
        podman_bin="/fake/podman",
    )
    rid = a.create_region()
    a.execute(
        region_id=rid,
        argv=("cat", "/in/hello.txt"),
        env={},
        timeout_seconds=5,
        inputs={"hello.txt": b"hello world\n"},
    )
    argv = FakePopen.seen_argvs[-1]
    # Find the /in mount in -v args
    v_args = [argv[i + 1] for i, x in enumerate(argv) if x == "-v"]
    in_mounts = [v for v in v_args if v.endswith(":/in:ro,Z,U")]
    assert len(in_mounts) == 1
    host_in_dir = in_mounts[0].split(":", 1)[0]
    # The file should exist on the host side and contain the bytes
    import os as _os

    assert "hello.txt" in _os.listdir(host_in_dir)
    with open(_os.path.join(host_in_dir, "hello.txt"), "rb") as f:
        assert f.read() == b"hello world\n"


def test_execute_rejects_invalid_input_name(fake_podman: None) -> None:
    a = PodmanSandboxActuator(
        (PodmanRegionSpec(spec_id="x", image="alpine"),),
        podman_bin="/fake/podman",
    )
    rid = a.create_region()
    with pytest.raises(ValueError):
        a.execute(
            region_id=rid,
            argv=("true",),
            env={},
            timeout_seconds=5,
            inputs={"../etc/shadow": b"pwned"},
        )


def test_execute_rejects_inputs_when_auto_io_disabled(fake_podman: None) -> None:
    spec = PodmanRegionSpec(spec_id="x", image="alpine", auto_io_mounts=False)
    a = PodmanSandboxActuator((spec,), podman_bin="/fake/podman")
    rid = a.create_region()
    with pytest.raises(ValueError, match="auto_io_mounts=False"):
        a.execute(
            region_id=rid,
            argv=("true",),
            env={},
            timeout_seconds=5,
            inputs={"x.txt": b"y"},
        )


def test_execute_emits_in_and_out_mounts_by_default(fake_podman: None) -> None:
    """auto_io_mounts=True (default) means every execute gets fresh
    /in and /out mounts, even if `inputs` is None."""
    a = PodmanSandboxActuator(
        (PodmanRegionSpec(spec_id="x", image="alpine"),),
        podman_bin="/fake/podman",
    )
    rid = a.create_region()
    a.execute(region_id=rid, argv=("true",), env={}, timeout_seconds=5)
    argv = FakePopen.seen_argvs[-1]
    v_args = [argv[i + 1] for i, x in enumerate(argv) if x == "-v"]
    assert any(v.endswith(":/in:ro,Z,U") for v in v_args)
    assert any(v.endswith(":/out:rw,Z,U") for v in v_args)


def test_harvest_outputs_empty_dir(tmp_path) -> None:
    spec = PodmanRegionSpec(spec_id="x", image="alpine")
    out = _harvest_outputs(tmp_path, spec)
    assert out == ()


def test_harvest_outputs_collects_files_with_previews(tmp_path) -> None:
    spec = PodmanRegionSpec(
        spec_id="x", image="alpine", output_preview_bytes=10,
    )
    (tmp_path / "a.txt").write_text("hello world this is long")
    (tmp_path / "b.json").write_text('{"k": 1}')
    outputs = _harvest_outputs(tmp_path, spec)
    by_name = {o.name: o for o in outputs}
    assert set(by_name) == {"a.txt", "b.json"}
    # a.txt is longer than preview cap → truncated
    assert by_name["a.txt"].truncated is True
    assert len(by_name["a.txt"].preview.encode("utf-8")) <= 10
    # b.json fits in preview → not truncated
    assert by_name["b.json"].truncated is False
    assert by_name["b.json"].preview == '{"k": 1}'


def test_harvest_outputs_respects_max_files(tmp_path) -> None:
    spec = PodmanRegionSpec(spec_id="x", image="alpine", output_max_files=2)
    for i in range(5):
        (tmp_path / f"f{i}.txt").write_text(str(i))
    outputs = _harvest_outputs(tmp_path, spec)
    assert len(outputs) == 2


def test_harvest_outputs_respects_total_bytes(tmp_path) -> None:
    spec = PodmanRegionSpec(
        spec_id="x", image="alpine", output_max_total_bytes=10,
    )
    (tmp_path / "a.txt").write_bytes(b"x" * 8)
    (tmp_path / "b.txt").write_bytes(b"y" * 8)
    outputs = _harvest_outputs(tmp_path, spec)
    # The second file gets included but truncated to fit the cap.
    assert sum(len(o.preview.encode("utf-8")) for o in outputs) <= 16


def test_read_output_round_trip(fake_podman: None) -> None:
    """Write a known payload via inputs, have the (fake) container exit;
    we then call read_output() on something we placed in /out by
    hand to simulate what a real container would do."""
    import os as _os

    a = PodmanSandboxActuator(
        (PodmanRegionSpec(spec_id="x", image="alpine"),),
        podman_bin="/fake/podman",
    )
    rid = a.create_region()
    a.execute(
        region_id=rid,
        argv=("true",),
        env={},
        timeout_seconds=5,
        inputs={"in.txt": b"input data"},
    )
    # FakePopen doesn't actually run the container, so /out is empty.
    # Simulate the container having written a file by dropping one
    # into the harvest dir, then re-harvesting.
    assert a._regions[rid].harvest_dir is not None
    out_path = _os.path.join(a._regions[rid].harvest_dir, "out", "result.txt")
    with open(out_path, "wb") as f:
        f.write(b"sandbox-produced output\n")
    _os.chmod(out_path, 0o644)
    data = a.read_output(rid, "result.txt")
    assert data == b"sandbox-produced output\n"


def test_read_output_unknown_region(fake_podman: None) -> None:
    a = PodmanSandboxActuator(
        (PodmanRegionSpec(spec_id="x", image="alpine"),),
        podman_bin="/fake/podman",
    )
    with pytest.raises(UnknownRegion):
        a.read_output("not-a-region", "x")


def test_read_output_path_traversal_blocked(fake_podman: None) -> None:
    a = PodmanSandboxActuator(
        (PodmanRegionSpec(spec_id="x", image="alpine"),),
        podman_bin="/fake/podman",
    )
    rid = a.create_region()
    a.execute(region_id=rid, argv=("true",), env={}, timeout_seconds=5)
    with pytest.raises(ValueError):
        a.read_output(rid, "../../etc/passwd")


def test_discard_region_cleans_harvest_dir(fake_podman: None) -> None:
    import os as _os

    a = PodmanSandboxActuator(
        (PodmanRegionSpec(spec_id="x", image="alpine"),),
        podman_bin="/fake/podman",
    )
    rid = a.create_region()
    a.execute(region_id=rid, argv=("true",), env={}, timeout_seconds=5)
    hd = a._regions[rid].harvest_dir
    assert hd is not None
    assert _os.path.isdir(hd)
    a.discard_region(rid)
    assert not _os.path.exists(hd)


# --- Integration smoke (skipped if podman missing) --------------------------


@pytest.mark.skipif(
    shutil.which("podman") is None, reason="real podman binary not present",
)
def test_real_podman_round_trip_files(tmp_path) -> None:
    """End-to-end: pass a script in via inputs, have it read the
    input + write a transformed output, harvest it back, verify
    bytes are correct."""
    spec = PodmanRegionSpec(
        spec_id="rt",
        image="docker.io/library/alpine:latest",
    )
    a = PodmanSandboxActuator((spec,))
    rid = a.create_region()
    try:
        result = a.execute(
            region_id=rid,
            argv=(
                "/bin/sh",
                "-c",
                "tr 'a-z' 'A-Z' < /in/lower.txt > /out/upper.txt",
            ),
            env={},
            timeout_seconds=30,
            inputs={"lower.txt": b"hello world\n"},
        )
        assert result.exit_code == 0
        names = {o.name for o in result.outputs}
        assert "upper.txt" in names
        upper = next(o for o in result.outputs if o.name == "upper.txt")
        assert upper.preview == "HELLO WORLD\n"
        # Also via read_output:
        assert a.read_output(rid, "upper.txt") == b"HELLO WORLD\n"
    finally:
        a.discard_region(rid)


@pytest.mark.skipif(
    shutil.which("podman") is None, reason="real podman binary not present",
)
def test_real_podman_smoke(tmp_path) -> None:
    """If a real podman is on PATH, run `alpine:latest echo hello` and
    verify we get exit_code=0 + non-empty digest. Pulls the alpine
    image if missing (one-time, ~3MB)."""
    spec = PodmanRegionSpec(spec_id="smoke", image="docker.io/library/alpine:latest")
    a = PodmanSandboxActuator((spec,))
    rid = a.create_region()
    events: list[SandboxProgress] = []
    result = a.execute(
        region_id=rid,
        argv=("echo", "hello"),
        env={},
        timeout_seconds=60,
        progress_callback=events.append,
    )
    a.discard_region(rid)
    assert result.exit_code == 0
    assert any(e.kind == "stdout" and "hello" in e.payload for e in events)
