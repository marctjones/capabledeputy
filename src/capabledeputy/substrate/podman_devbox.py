"""Podman devbox — persistent counterpart to PodmanSandboxActuator.

Where `PodmanSandboxActuator` runs disposable `podman run --rm`
containers, this module manages LONG-LIVED containers keyed by
(session_id, spec_id). The container is started once with
`sleep infinity` as its keep-alive process; subsequent `exec(...)`
calls re-enter via `podman exec`; the container survives across
agent turns. Tear-down happens at end-of-session (via
`stop_session`) or by explicit `stop`. The `/work` volume is bind-
mounted from a session-scoped host dir under
`$XDG_STATE_HOME/capdep/devbox/<session>/<spec>/work` and persists
exactly as long as the host dir does — discard semantics are the
operator's call, not the container's.

Shape mirrors PodmanSandboxActuator deliberately: same
`PodmanRegionSpec` config, same `--cap-drop=ALL` / `no-new-privileges`
/ unprivileged-uid hardening, same `_force_remove_container` cleanup.
Differences from one-shot:
  * No `--rm` flag (the container survives the start call).
  * No `--read-only` rootfs — dev workflows need a writeable / for
    package installs, build artifacts, etc. Per-spec hardening on
    capabilities + uid is still applied.
  * No `/in /out` auto-mounts (devbox uses `/work` for IO; one-shot
    inputs/outputs make less sense for a persistent container).
  * `exec(...)` runs `podman exec` against the live container — no
    fresh `podman run` per call.

Threading: registry mutations take `_lock`. Each `exec()` spawns
its own subprocess and drains its own pipes; concurrent execs
against the SAME container are allowed (podman supports it).
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from capabledeputy.substrate.podman_sandbox import (
    PodmanNotAvailable,
    PodmanRegionSpec,
    UnknownRegion,
)


@dataclass(frozen=True)
class DevboxExecResult:
    """Result of one `podman exec` invocation against a live devbox.

    `cancelled` distinguishes a SIGTERM/SIGKILL from the host side
    (e.g. a session cancel) from a natural in-container exit.
    `timed_out` indicates the per-call timeout fired and we killed
    the exec (NOT the container — the container keeps running).
    """

    container_name: str
    exit_code: int
    stdout: bytes
    stderr: bytes
    cancelled: bool = False
    timed_out: bool = False


@dataclass
class _LiveDevbox:
    """Per-(session, spec) live container record."""

    session_id: UUID
    spec: PodmanRegionSpec
    container_name: str
    workspace_host_path: Path
    started_at: float
    # Currently executing `podman exec` subprocess, if any. Used by
    # the future cancel path; today the field is set/cleared on
    # entry/exit of `exec()` for visibility in `list_session()`.
    current_exec: subprocess.Popen | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class PodmanDevbox:
    """Manager for long-lived per-session-per-spec containers.

    Construction takes the same `PodmanRegionSpec` tuple as
    `PodmanSandboxActuator` so a single daemon config drives both.
    `start_or_get` is idempotent — calling it twice with the same
    (session, spec) returns the existing container if alive.

    The host-side state is `(session_id, spec_id) → _LiveDevbox`.
    On session end the daemon calls `stop_session(session_id)` to
    `podman rm -f` every container belonging to that session;
    workspace dirs are left on disk unless `purge_workspace=True`
    is passed (default: preserve so the operator can recover work).
    """

    def __init__(
        self,
        specs: tuple[PodmanRegionSpec, ...],
        *,
        workspace_root: Path | None = None,
        podman_bin: str | None = None,
    ) -> None:
        for s in specs:
            s.validate()
        self._specs: dict[str, PodmanRegionSpec] = {s.spec_id: s for s in specs}
        if len(self._specs) != len(specs):
            raise ValueError("duplicate region spec_id in specs list")
        self._podman_bin = podman_bin or shutil.which("podman") or "podman"
        if not self._verify_podman_present():
            raise PodmanNotAvailable(
                f"podman binary not found or not executable at "
                f"{self._podman_bin!r}; install rootless Podman before "
                "enabling provider=podman",
            )
        self._workspace_root = workspace_root or _default_workspace_root()
        self._workspace_root.mkdir(parents=True, exist_ok=True)
        self._live: dict[tuple[UUID, str], _LiveDevbox] = {}
        self._lock = threading.Lock()

    # ---- discovery ----

    def list_specs(self) -> tuple[str, ...]:
        """All registered devbox specs, sorted. Mirrors what an
        operator declared in daemon.yaml — useful to the
        `devbox.list` tool when no session-specific containers
        are live yet."""
        return tuple(sorted(self._specs))

    def list_session(self, session_id: UUID) -> tuple[dict[str, str | bool], ...]:
        """All live devbox containers for `session_id`. Returns a
        tuple of dicts safe to surface to the agent: spec_id,
        container_name, workspace_host_path, is_alive. Sorted by
        spec_id for stable LLM output."""
        with self._lock:
            entries = [
                (spec_id, live) for (sid, spec_id), live in self._live.items() if sid == session_id
            ]
        entries.sort(key=lambda e: e[0])
        out: list[dict[str, str | bool]] = []
        for spec_id, live in entries:
            out.append(
                {
                    "spec_id": spec_id,
                    "container_name": live.container_name,
                    "workspace_host_path": str(live.workspace_host_path),
                    "is_alive": self._is_alive(live.container_name),
                },
            )
        return tuple(out)

    # ---- lifecycle ----

    def start_or_get(self, session_id: UUID, spec_id: str) -> _LiveDevbox:
        """Idempotent: if a live container already exists for
        (session_id, spec_id) and `podman inspect` confirms it's
        running, return it; otherwise spin up a fresh one.

        Workspace dir is created at
        `<workspace_root>/<session_id>/<spec_id>/work` and bind-
        mounted at `/work` inside the container with `:U` so the
        unprivileged in-container uid owns it.
        """
        if spec_id not in self._specs:
            raise UnknownRegion(
                f"unknown devbox spec {spec_id!r}; declared: {sorted(self._specs)!r}",
            )
        key = (session_id, spec_id)
        with self._lock:
            existing = self._live.get(key)
        if existing is not None and self._is_alive(existing.container_name):
            return existing
        # Either no record, or the record points to a dead container
        # (operator may have `podman rm`'d it manually). Reap any
        # stale name then start fresh.
        if existing is not None:
            self._force_remove_container(existing.container_name)
            with self._lock:
                self._live.pop(key, None)
        spec = self._specs[spec_id]
        container_name = self._container_name_for(session_id, spec_id)
        workspace = self._workspace_root / str(session_id) / spec_id / "work"
        workspace.mkdir(parents=True, exist_ok=True)
        # Best-effort: a leftover container with this name (e.g. a
        # previous daemon crash) would make `podman run` collide. The
        # remove is idempotent — no error if absent.
        self._force_remove_container(container_name)
        run_argv = self._build_start_argv(spec, container_name, workspace)
        try:
            result = subprocess.run(
                run_argv,
                capture_output=True,
                timeout=30,
                check=False,
            )
        except FileNotFoundError as e:
            raise PodmanNotAvailable(
                f"podman binary disappeared: {self._podman_bin!r}",
            ) from e
        if result.returncode != 0:
            raise PodmanStartError(
                f"podman run -d failed for spec {spec_id!r}: "
                f"exit={result.returncode} stderr={result.stderr.decode(errors='replace')!r}",
            )
        live = _LiveDevbox(
            session_id=session_id,
            spec=spec,
            container_name=container_name,
            workspace_host_path=workspace,
            started_at=time.time(),
        )
        with self._lock:
            self._live[key] = live
        return live

    def exec(
        self,
        session_id: UUID,
        spec_id: str,
        *,
        argv: tuple[str, ...],
        timeout_seconds: int = 30,
        env: dict[str, str] | None = None,
        stdin_bytes: bytes | None = None,
        workdir: str = "/work",
    ) -> DevboxExecResult:
        """Run `argv` inside the live container for (session, spec)
        via `podman exec`. Auto-starts the container if not already
        running.

        `workdir` defaults to `/work` (the persistent volume) so
        relative paths resolve where the agent expects. Override to
        `/tmp` etc. when the agent specifically wants a scratch
        location.
        """
        live = self.start_or_get(session_id, spec_id)
        exec_argv = self._build_exec_argv(live, argv, env or {}, workdir=workdir)

        stdout_buf = bytearray()
        stderr_buf = bytearray()
        cancelled = False
        timed_out = False
        exit_code = -1

        try:
            proc = subprocess.Popen(
                exec_argv,
                stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except FileNotFoundError as e:
            raise PodmanNotAvailable(
                f"podman binary disappeared: {self._podman_bin!r}",
            ) from e

        with live.lock:
            live.current_exec = proc

        stdin_writer: threading.Thread | None = None
        if stdin_bytes is not None and proc.stdin is not None:

            def _write_stdin() -> None:
                try:
                    assert proc.stdin is not None
                    proc.stdin.write(stdin_bytes)
                    proc.stdin.close()
                except (BrokenPipeError, OSError):
                    pass

            stdin_writer = threading.Thread(target=_write_stdin, daemon=True)
            stdin_writer.start()

        def _drain(stream, buf: bytearray) -> None:
            try:
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        break
                    buf.extend(chunk)
            except Exception:
                pass

        t_out = threading.Thread(
            target=_drain,
            args=(proc.stdout, stdout_buf),
            daemon=True,
        )
        t_err = threading.Thread(
            target=_drain,
            args=(proc.stderr, stderr_buf),
            daemon=True,
        )
        t_out.start()
        t_err.start()

        deadline = time.monotonic() + timeout_seconds
        while True:
            rc = proc.poll()
            if rc is not None:
                exit_code = rc
                break
            if time.monotonic() >= deadline:
                timed_out = True
                # Kill JUST the exec process, NOT the container.
                # The exec child will be reaped; the keep-alive
                # `sleep infinity` process keeps the container up.
                with contextlib.suppress(Exception):
                    proc.terminate()
                try:
                    exit_code = proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    exit_code = proc.wait()
                break
            time.sleep(0.05)

        t_out.join(timeout=2)
        t_err.join(timeout=2)
        if stdin_writer is not None:
            stdin_writer.join(timeout=2)

        with live.lock:
            live.current_exec = None

        # 137 / 143 from `podman exec` mean the exec process was
        # signalled — treat as a cancel UNLESS we just timed out
        # (timed_out flag wins).
        if not timed_out and exit_code in (137, 143):
            cancelled = True

        return DevboxExecResult(
            container_name=live.container_name,
            exit_code=exit_code,
            stdout=bytes(stdout_buf),
            stderr=bytes(stderr_buf),
            cancelled=cancelled,
            timed_out=timed_out,
        )

    def stop(
        self,
        session_id: UUID,
        spec_id: str,
        *,
        purge_workspace: bool = False,
    ) -> bool:
        """Tear down the container for (session, spec). Returns True
        iff a container was found and removed. Workspace dir is
        preserved by default so the operator can recover work; pass
        `purge_workspace=True` to also remove the host dir."""
        key = (session_id, spec_id)
        with self._lock:
            live = self._live.pop(key, None)
        if live is None:
            return False
        self._force_remove_container(live.container_name)
        if purge_workspace:
            shutil.rmtree(live.workspace_host_path, ignore_errors=True)
        return True

    def stop_session(
        self,
        session_id: UUID,
        *,
        purge_workspace: bool = False,
    ) -> int:
        """Tear down every devbox container belonging to
        `session_id`. Returns the count torn down. Called by the
        daemon's session-end hook."""
        with self._lock:
            keys = [k for k in self._live if k[0] == session_id]
        n = 0
        for _, spec_id in keys:
            if self.stop(session_id, spec_id, purge_workspace=purge_workspace):
                n += 1
        return n

    # ---- internals ----

    def _verify_podman_present(self) -> bool:
        try:
            result = subprocess.run(
                [self._podman_bin, "--version"],
                capture_output=True,
                timeout=5,
                check=False,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _is_alive(self, container_name: str) -> bool:
        """`podman inspect -f {{.State.Running}}` returns "true" /
        "false" on stdout. Anything else (container missing, podman
        error) → not alive."""
        try:
            result = subprocess.run(
                [
                    self._podman_bin,
                    "inspect",
                    "-f",
                    "{{.State.Running}}",
                    container_name,
                ],
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0 and result.stdout.strip() == b"true"

    def _force_remove_container(self, container_name: str) -> None:
        for sub in (["kill", container_name], ["rm", "-f", container_name]):
            with contextlib.suppress(FileNotFoundError, subprocess.TimeoutExpired):
                subprocess.run(
                    [self._podman_bin, *sub],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )

    def _container_name_for(self, session_id: UUID, spec_id: str) -> str:
        """Deterministic container name so `podman ps` shows
        recognisable entries and a daemon restart can reap leftovers
        by name. UUID gives global uniqueness across capdep instances
        on the same host."""
        return f"capdep-devbox-{session_id}-{spec_id}"

    def _build_start_argv(
        self,
        spec: PodmanRegionSpec,
        container_name: str,
        workspace: Path,
    ) -> list[str]:
        cmd: list[str] = [
            self._podman_bin,
            "run",
            "-d",  # detached — return immediately, container keeps running
            # No `--rm`: the container must survive the start call.
            # No `--read-only` rootfs: dev work needs writeable /tmp
            # and /var for package installs, build artifacts, etc.
            # Capability hardening still applies (below).
            "--cap-drop=ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "65534:65534",
            f"--memory={spec.memory_mb}m",
            f"--cpus={spec.cpus}",
            f"--pids-limit={spec.pids_limit}",
            "--name",
            container_name,
            "--network",
            spec.network,
            # Persistent workspace. `:U` chowns the host dir to the
            # container's uid (rootless Podman); `:Z` relabels for
            # SELinux (harmless on non-SELinux hosts).
            "-v",
            f"{workspace}:/work:rw,Z,U",
        ]
        for m in spec.mounts:
            ro = "ro" if m.read_only else "rw"
            cmd.extend(["-v", f"{m.host_path}:{m.container_path}:{ro},Z"])
        for name in spec.env_allowlist:
            val = os.environ.get(name)
            if val is not None:
                cmd.extend(["-e", f"{name}={val}"])
        cmd.extend(spec.extra_args)
        cmd.append(spec.image)
        # Keep-alive. `sleep infinity` works on busybox (alpine) and
        # GNU coreutils (debian/ubuntu/fedora/python:slim/node-slim).
        cmd.extend(["sleep", "infinity"])
        return cmd

    def _build_exec_argv(
        self,
        live: _LiveDevbox,
        argv: tuple[str, ...],
        env: dict[str, str],
        *,
        workdir: str,
    ) -> list[str]:
        cmd: list[str] = [
            self._podman_bin,
            "exec",
            "-i",  # accept stdin
            "-w",
            workdir,
            "--user",
            "65534:65534",
        ]
        for name in live.spec.env_allowlist:
            val = env.get(name) if env else None
            if val is None:
                val = os.environ.get(name)
            if val is not None:
                cmd.extend(["-e", f"{name}={val}"])
        cmd.append(live.container_name)
        cmd.extend(argv)
        return cmd


class PodmanStartError(RuntimeError):
    """`podman run -d` exited non-zero when starting a devbox.
    The stderr text is included so operators can diagnose image
    pull failures, port conflicts, etc."""


def _default_workspace_root() -> Path:
    """`$XDG_STATE_HOME/capdep/devbox/` per XDG basedir spec,
    falling back to `~/.local/state/capdep/devbox/`. Per-session
    subdirs land under here."""
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg) / "capdep" / "devbox"
    return Path.home() / ".local" / "state" / "capdep" / "devbox"
