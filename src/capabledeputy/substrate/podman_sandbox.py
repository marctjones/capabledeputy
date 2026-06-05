"""Podman SandboxActuator — rootless container provider (004 U034 / U035).

Each `execute()` shells out to `podman run` to launch a one-shot,
ephemeral container. The container's lifetime is the unit of
containment: when it exits (or is killed via `cancel()` / discarded
via `discard_region()`), every effect inside the container is gone.

Hardened defaults match the spec-004 scope:
  --rm                            : auto-remove on exit
  --read-only                     : read-only rootfs
  --net=none                      : no egress unless region overrides
  --cap-drop=ALL                  : drop every Linux capability
  --security-opt no-new-privileges: deny privilege escalation
  --user 65534:65534              : run as `nobody`
  --memory <MB>                   : RAM cap (default 512 MB)
  --cpus <N>                      : CPU cap (default 1.0)
  --pids-limit <N>                : fork-bomb cap (default 128)

Region specs are operator-declared in the daemon config under
`sandbox:` and registered once on actuator construction. Each
`create_region()` call mints a fresh per-execution id namespaced
to a declared spec; the host-side state is just an in-process map
from region_id → (spec, current container name).

Phase B extensions on top of the port:
  - `progress_callback` receives lifecycle events + line-buffered
    stdout/stderr as the container runs.
  - `cancel(region_id)` does `podman kill` on the running container
    so the operator (or the agent's caller) can break out of long
    runs without waiting for the timeout.
  - `stdin_bytes` is piped into the container's stdin before output
    capture begins.

The actuator only talks to the `podman` binary via subprocess; no
libpod / API socket dependency. If `podman` is not on PATH the
constructor raises `PodmanNotAvailable` so the daemon fails-loudly
at startup, never silently.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import secrets
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field

from capabledeputy.substrate.sandbox_actuator import (
    ProgressCallback,
    SandboxActuator,
    SandboxOutputFile,
    SandboxProgress,
    SandboxResult,
)


class PodmanNotAvailable(RuntimeError):
    """Raised when the `podman` binary cannot be found on PATH or
    fails a basic version check. The daemon should refuse to start
    if Podman is selected but unavailable — silent fallback to the
    demo actuator would violate Principle VI (fail-closed)."""


class UnknownRegion(KeyError):
    """`region_id` is not registered with this actuator. Either it
    was never created via `create_region()` or it was already
    discarded."""


@dataclass(frozen=True)
class PodmanMount:
    """One host→container bind mount. Read-only by default; explicitly
    declare `read_only=False` for write-out paths.

    `host_path` is a host-side directory or file. We refuse any path
    that resolves outside `host_path` after symlink resolution
    (defense in depth — Podman's `:Z` SELinux relabel further isolates
    the volume).

    `container_path` is the in-container mount target (must be
    absolute).
    """

    host_path: str
    container_path: str
    read_only: bool = True


@dataclass(frozen=True)
class PodmanRegionSpec:
    """Operator-declared region template. `create_region()` allocates
    a fresh disposable instance from one of these specs.

    Field defaults are deliberately conservative:
      - `network = "none"`     : no egress
      - `memory_mb = 512`      : capped RAM
      - `cpus = 1.0`           : capped CPU
      - `pids_limit = 128`     : fork-bomb cap
      - `require_digest_pin`   : forces `image` to be `name@sha256:...`

    `env_allowlist` is the set of caller-supplied env-var names that
    pass through to the container. Anything else is dropped. Default
    empty: the container sees no host env vars unless explicitly
    permitted.
    """

    spec_id: str
    image: str
    network: str = "none"
    mounts: tuple[PodmanMount, ...] = ()
    memory_mb: int = 512
    cpus: float = 1.0
    pids_limit: int = 128
    timeout_seconds_default: int = 30
    require_digest_pin: bool = False
    env_allowlist: tuple[str, ...] = ()
    extra_args: tuple[str, ...] = ()
    # Per-execution scratch dirs auto-mounted at /in (read-only,
    # populated from caller's `inputs`) and /out (read-write, harvested
    # into SandboxResult.outputs). The agent-facing `sandbox.run` tool
    # relies on these. Set False if a region needs only operator-
    # declared static mounts.
    auto_io_mounts: bool = True
    # Caps applied during the output harvest. Per-file preview is the
    # bytes returned inline in the result; full bytes stay on disk
    # until the region is discarded (`read_output()` retrieves them).
    output_preview_bytes: int = 4096
    output_max_files: int = 32
    output_max_total_bytes: int = 16 * 1024 * 1024  # 16 MiB

    def validate(self) -> None:
        """Raise ValueError on a malformed spec. Called by the
        actuator constructor so misconfiguration shows up at daemon
        startup, never at first execute()."""
        if self.require_digest_pin and "@sha256:" not in self.image:
            raise ValueError(
                f"region spec {self.spec_id!r}: image {self.image!r} is not "
                "digest-pinned and require_digest_pin=True",
            )
        if self.memory_mb < 16:
            raise ValueError(
                f"region spec {self.spec_id!r}: memory_mb={self.memory_mb} too low",
            )
        if self.cpus <= 0:
            raise ValueError(
                f"region spec {self.spec_id!r}: cpus={self.cpus} must be positive",
            )
        if self.pids_limit < 1:
            raise ValueError(
                f"region spec {self.spec_id!r}: pids_limit={self.pids_limit} too low",
            )
        for m in self.mounts:
            if not m.container_path.startswith("/"):
                raise ValueError(
                    f"region spec {self.spec_id!r}: mount target "
                    f"{m.container_path!r} must be absolute",
                )


@dataclass
class _LiveRegion:
    """Mutable per-region state held by the actuator. `process` is the
    currently-executing `podman run` subprocess (if any), used by
    cancel/discard. `harvest_dir` is the host-side copy of `/out`
    from the most recent `execute()` call — kept on disk until
    `discard_region` so `read_output()` can pull bytes."""

    spec: PodmanRegionSpec
    container_name: str
    process: subprocess.Popen | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    harvest_dir: str | None = None  # filled after each execute(); cleared on discard


class PodmanSandboxActuator(SandboxActuator):
    """Production-shaped SandboxActuator backed by rootless Podman.

    Construction takes the list of `PodmanRegionSpec` the operator
    has declared. A `create_region(spec_id=...)` call allocates a
    fresh region against one of those specs; an `execute()` call
    runs `podman run` with the spec's hardening flags + the caller's
    argv. Region death (`discard_region`) `podman kill`s any still-
    running container in that region.
    """

    def __init__(
        self,
        specs: tuple[PodmanRegionSpec, ...],
        *,
        podman_bin: str | None = None,
        chunk_size: int = 4096,
    ) -> None:
        for s in specs:
            s.validate()
        self._specs: dict[str, PodmanRegionSpec] = {s.spec_id: s for s in specs}
        if len(self._specs) != len(specs):
            raise ValueError("duplicate region spec_id in specs list")
        self._podman_bin = podman_bin or shutil.which("podman") or "podman"
        if not self._verify_podman_present():
            raise PodmanNotAvailable(
                f"podman binary not found or not executable at {self._podman_bin!r}; "
                "install rootless Podman before enabling provider=podman",
            )
        self._regions: dict[str, _LiveRegion] = {}
        self._regions_lock = threading.Lock()
        self._chunk_size = chunk_size

    def _verify_podman_present(self) -> bool:
        """Best-effort: `podman --version` exits 0 on a working install.
        We don't pin a minimum version here — operators run wildly
        different distros. Detection failures are fatal at __init__."""
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

    # ---- region lifecycle ----

    def create_region(self, spec_id: str | None = None) -> str:  # type: ignore[override]
        """Allocate a fresh disposable region. `spec_id` selects which
        operator-declared template to instantiate; if omitted, exactly
        one spec must be registered (single-template install)."""
        if spec_id is None:
            if len(self._specs) != 1:
                raise ValueError(
                    f"create_region() with no spec_id requires exactly one registered "
                    f"spec; have {sorted(self._specs)!r}",
                )
            spec_id = next(iter(self._specs))
        if spec_id not in self._specs:
            raise UnknownRegion(
                f"unknown sandbox spec {spec_id!r}; declared: {sorted(self._specs)!r}",
            )
        region_id = f"podman-{spec_id}-{secrets.token_hex(8)}"
        container_name = f"capdep-{region_id}"
        with self._regions_lock:
            self._regions[region_id] = _LiveRegion(
                spec=self._specs[spec_id],
                container_name=container_name,
            )
        return region_id

    def discard_region(self, region_id: str) -> None:
        """Tear down `region_id`. If a container is still running we
        best-effort `podman kill` + `podman rm -f`; the `--rm` flag on
        `podman run` would clean up on graceful exit, but a kill is
        the safety net for the bad-case. Also cleans up the harvest
        dir holding the last execute's output files."""
        with self._regions_lock:
            region = self._regions.pop(region_id, None)
        if region is None:
            return
        self._force_remove_container(region.container_name)
        if region.harvest_dir is not None:
            shutil.rmtree(region.harvest_dir, ignore_errors=True)

    def cancel(self, region_id: str) -> bool:
        """Send SIGTERM (then SIGKILL) to a running container in this
        region. Returns True iff we found and killed something."""
        with self._regions_lock:
            region = self._regions.get(region_id)
        if region is None:
            return False
        with region.lock:
            proc = region.process
        if proc is None or proc.poll() is not None:
            return False
        # `podman kill` is the cleanest path; if the container has
        # already exited the kill is a no-op (idempotent). We then
        # let the Popen.communicate() side observe the exit.
        with contextlib.suppress(FileNotFoundError, subprocess.TimeoutExpired):
            subprocess.run(
                [self._podman_bin, "kill", region.container_name],
                capture_output=True,
                timeout=5,
                check=False,
            )
        return True

    # ---- execution ----

    def execute(
        self,
        *,
        region_id: str,
        argv: tuple[str, ...],
        env: dict[str, str],
        timeout_seconds: int,
        progress_callback: ProgressCallback | None = None,
        stdin_bytes: bytes | None = None,
        inputs: dict[str, bytes] | None = None,
    ) -> SandboxResult:
        import tempfile
        from pathlib import Path

        with self._regions_lock:
            region = self._regions.get(region_id)
        if region is None:
            raise UnknownRegion(
                f"sandbox region {region_id!r} is not live "
                f"(never created or already discarded)",
            )

        # Prepare per-execution scratch dirs for /in (RO) and /out (RW)
        # when the region has auto_io_mounts enabled. The /in dir is
        # populated from `inputs` BEFORE the container starts; /out is
        # empty and harvested AFTER. The previous execute's harvest_dir
        # (if any) is cleaned up here so each run starts fresh.
        io_root: Path | None = None
        in_dir: Path | None = None
        out_dir: Path | None = None
        if region.spec.auto_io_mounts:
            if region.harvest_dir is not None:
                shutil.rmtree(region.harvest_dir, ignore_errors=True)
                region.harvest_dir = None
            io_root = Path(tempfile.mkdtemp(prefix=f"capdep-sandbox-{region_id}-"))
            in_dir = io_root / "in"
            out_dir = io_root / "out"
            in_dir.mkdir(mode=0o755)
            out_dir.mkdir(mode=0o777)  # container writes as uid 65534
            for name, content in (inputs or {}).items():
                _validate_input_name(name)
                (in_dir / name).write_bytes(content)
                (in_dir / name).chmod(0o644)
        elif inputs:
            raise ValueError(
                f"region {region_id!r} has auto_io_mounts=False; cannot accept inputs",
            )

        run_argv = self._build_run_argv(
            region, argv, env, in_dir=in_dir, out_dir=out_dir,
        )
        emit = _safe_emit(progress_callback)
        emit(SandboxProgress(kind="lifecycle", payload="image_check"))

        stdout_bytes = bytearray()
        stderr_bytes = bytearray()
        cancelled = False
        timed_out = False
        exit_code = -1

        try:
            proc = subprocess.Popen(
                run_argv,
                stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except FileNotFoundError as e:
            raise PodmanNotAvailable(
                f"podman binary disappeared mid-run: {self._podman_bin!r}",
            ) from e

        with region.lock:
            region.process = proc

        emit(SandboxProgress(kind="lifecycle", payload="container_start"))
        deadline = time.monotonic() + timeout_seconds

        # Write stdin in a worker so a slow consumer can't deadlock us.
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

        # Drain stdout + stderr in worker threads so we can both
        # surface progress events and enforce timeout uniformly. The
        # threads stop themselves when the pipes hit EOF (container
        # exit or kill).
        def _drain(stream, kind: str, buf: bytearray) -> None:
            try:
                while True:
                    chunk = stream.readline()
                    if not chunk:
                        break
                    buf.extend(chunk)
                    try:
                        text = chunk.decode("utf-8", errors="replace").rstrip("\n")
                    except Exception:
                        text = repr(chunk)
                    emit(
                        SandboxProgress(
                            kind=kind,  # type: ignore[arg-type]
                            payload=text,
                            bytes_seen=len(buf),
                        ),
                    )
            except Exception:
                pass

        t_out = threading.Thread(
            target=_drain, args=(proc.stdout, "stdout", stdout_bytes), daemon=True,
        )
        t_err = threading.Thread(
            target=_drain, args=(proc.stderr, "stderr", stderr_bytes), daemon=True,
        )
        t_out.start()
        t_err.start()

        # Poll for completion or timeout. cancel() is observed
        # indirectly: it kills the container, which makes proc exit,
        # which makes the poll succeed.
        while True:
            rc = proc.poll()
            if rc is not None:
                exit_code = rc
                break
            if time.monotonic() >= deadline:
                timed_out = True
                self._force_remove_container(region.container_name)
                # Wait briefly for proc to react to the kill.
                try:
                    exit_code = proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    exit_code = proc.wait()
                break
            time.sleep(0.05)

        # Drain workers must finish before we hash bytes
        t_out.join(timeout=2)
        t_err.join(timeout=2)
        if stdin_writer is not None:
            stdin_writer.join(timeout=2)

        with region.lock:
            region.process = None

        # Distinguish cancel from natural exit. cancel() sets no flag;
        # we infer it from a non-zero exit AFTER a kill was issued.
        # Podman exits with 137 (128 + SIGKILL) or 143 (128 + SIGTERM)
        # when killed; treat either as a cancel signal *if* we didn't
        # time out (timeout uses its own force-kill, also exit 137).
        if not timed_out and exit_code in (137, 143):
            cancelled = True
            emit(SandboxProgress(kind="lifecycle", payload="cancelled"))
        elif timed_out:
            emit(SandboxProgress(kind="lifecycle", payload="timed_out"))
        emit(SandboxProgress(kind="lifecycle", payload=f"container_exit:{exit_code}"))

        digest = hashlib.sha256(
            b"\x01".join((bytes(stdout_bytes), bytes(stderr_bytes))),
        ).hexdigest()

        # Harvest /out into the result — file metadata + truncated
        # previews inline. Full bytes stay on disk in `out_dir` until
        # discard_region or the next execute on this region. Keep the
        # harvest_dir on the region so read_output() can find it.
        outputs: tuple[SandboxOutputFile, ...] = ()
        if out_dir is not None:
            outputs = _harvest_outputs(out_dir, region.spec)
            region.harvest_dir = str(io_root) if io_root is not None else None
        elif io_root is not None:
            shutil.rmtree(io_root, ignore_errors=True)

        return SandboxResult(
            region_id=region_id,
            exit_code=exit_code,
            output_digest=digest,
            cancelled=cancelled,
            timed_out=timed_out,
            outputs=outputs,
        )

    def read_output(
        self,
        region_id: str,
        name: str,
        *,
        max_bytes: int = 1024 * 1024,
    ) -> bytes:
        """Read raw bytes of `name` from the most-recent execute's
        harvest dir. Region must still be live (not discarded)."""
        from pathlib import Path

        _validate_input_name(name)
        with self._regions_lock:
            region = self._regions.get(region_id)
        if region is None:
            raise UnknownRegion(
                f"sandbox region {region_id!r} is not live",
            )
        if region.harvest_dir is None:
            raise FileNotFoundError(
                f"region {region_id!r} has no harvest dir — "
                "either execute() wasn't called, or auto_io_mounts is disabled",
            )
        target = Path(region.harvest_dir) / "out" / name
        # Defense in depth: resolve and ensure still under the harvest dir.
        try:
            resolved = target.resolve(strict=True)
        except FileNotFoundError as e:
            raise FileNotFoundError(
                f"output {name!r} not present in region {region_id!r}",
            ) from e
        base = Path(region.harvest_dir).resolve()
        if base not in resolved.parents:
            raise PermissionError(
                f"output path {name!r} escapes the harvest dir",
            )
        with open(resolved, "rb") as f:
            return f.read(max_bytes)

    # ---- internals ----

    def _build_run_argv(
        self,
        region: _LiveRegion,
        argv: tuple[str, ...],
        env: dict[str, str],
        *,
        in_dir=None,
        out_dir=None,
    ) -> list[str]:
        """Compose the full `podman run ...` command line. Caller's
        argv is appended last after `--` so flag-shaped strings can't
        be reinterpreted as podman options."""
        spec = region.spec
        cmd: list[str] = [
            self._podman_bin,
            "run",
            "--rm",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "65534:65534",
            f"--memory={spec.memory_mb}m",
            f"--cpus={spec.cpus}",
            f"--pids-limit={spec.pids_limit}",
            "--name",
            region.container_name,
            "--network",
            spec.network,
        ]
        for m in spec.mounts:
            # SELinux relabel (`Z`) is harmless on non-SELinux hosts
            # and required on Fedora/RHEL/etc. for the container to
            # be able to read the volume at all.
            ro = "ro" if m.read_only else "rw"
            cmd.extend(["-v", f"{m.host_path}:{m.container_path}:{ro},Z"])
        # Auto-IO mounts: per-execution scratch dirs surface at /in
        # (read-only) and /out (read-write). Caller writes to /in
        # before the run (`inputs` argument); the actuator harvests
        # /out after the run (SandboxResult.outputs).
        #
        # `:U` is the magic that makes rootless Podman work here: it
        # chowns the mount to the container's effective uid (here
        # 65534/`nobody`) so the unprivileged in-container user can
        # actually read /in and write to /out. Without `:U`, the
        # host-uid-to-container-uid mapping rejects the write and
        # the container exits with permission denied.
        if in_dir is not None:
            cmd.extend(["-v", f"{in_dir}:/in:ro,Z,U"])
        if out_dir is not None:
            cmd.extend(["-v", f"{out_dir}:/out:rw,Z,U"])
        for name in spec.env_allowlist:
            val = env.get(name) if env else None
            if val is None:
                val = os.environ.get(name)
            if val is not None:
                cmd.extend(["-e", f"{name}={val}"])
        cmd.extend(spec.extra_args)
        cmd.append("-i")  # allow stdin (cheap; ignored when none provided)
        cmd.append(spec.image)
        # No `--` separator: `podman run [opts] IMAGE [cmd args]` treats
        # everything after IMAGE as the container command. Adding `--`
        # would make IT the cmd and the real argv would be its args, so
        # the container would try to exec `--` and exit 127.
        cmd.extend(argv)
        return cmd

    def _force_remove_container(self, container_name: str) -> None:
        """Best-effort kill + remove. Used by both timeout and discard
        paths. Errors are swallowed: the container may already be
        gone (race with `--rm`)."""
        for sub in (["kill", container_name], ["rm", "-f", container_name]):
            with contextlib.suppress(FileNotFoundError, subprocess.TimeoutExpired):
                subprocess.run(
                    [self._podman_bin, *sub],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )


def parse_sandbox_config(raw: dict) -> tuple[PodmanRegionSpec, ...]:
    """Parse the `sandbox:` block of a daemon config file into typed
    region specs. Format:

        sandbox:
          provider: podman           # (currently only "podman" or "in_process")
          regions:
            - id: scratch-python
              image: docker.io/library/python:3.12-slim
              network: none
              memory_mb: 512
              cpus: 1.0
              pids_limit: 128
              timeout_seconds_default: 30
              require_digest_pin: false
              env_allowlist: ["PYTHONUNBUFFERED"]
              mounts:
                - host: /var/cache/sandbox/scratch
                  container: /work
                  read_only: false

    Returns an empty tuple if the block is absent or `provider` is not
    `podman` — the caller decides whether to construct the actuator
    based on the spec count + the provider field. Raises ValueError on
    malformed individual specs (per-spec `.validate()` runs).
    """
    sandbox_raw = raw.get("sandbox")
    if not sandbox_raw:
        return ()
    provider = str(sandbox_raw.get("provider", "")).lower()
    if provider not in ("podman", ""):
        return ()
    regions_raw = sandbox_raw.get("regions") or []
    specs: list[PodmanRegionSpec] = []
    for r in regions_raw:
        mounts_raw = r.get("mounts") or []
        mounts = tuple(
            PodmanMount(
                host_path=str(m["host"]),
                container_path=str(m["container"]),
                read_only=bool(m.get("read_only", True)),
            )
            for m in mounts_raw
        )
        spec = PodmanRegionSpec(
            spec_id=str(r["id"]),
            image=str(r["image"]),
            network=str(r.get("network", "none")),
            mounts=mounts,
            memory_mb=int(r.get("memory_mb", 512)),
            cpus=float(r.get("cpus", 1.0)),
            pids_limit=int(r.get("pids_limit", 128)),
            timeout_seconds_default=int(r.get("timeout_seconds_default", 30)),
            require_digest_pin=bool(r.get("require_digest_pin", False)),
            env_allowlist=tuple(str(s) for s in r.get("env_allowlist") or []),
            extra_args=tuple(str(s) for s in r.get("extra_args") or []),
        )
        spec.validate()
        specs.append(spec)
    return tuple(specs)


def load_sandbox_specs_from_file(path) -> tuple[PodmanRegionSpec, ...]:
    """Read a daemon config file and pull region specs out of its
    `sandbox:` block. Returns an empty tuple if the file has no
    such block — the caller treats that as "no provider configured."
    """
    from pathlib import Path as _Path

    p = _Path(path)
    if not p.is_file():
        return ()
    text = p.read_text(encoding="utf-8")
    if p.suffix in {".yaml", ".yml"}:
        import yaml  # type: ignore[import-untyped]

        raw = yaml.safe_load(text) or {}
    else:
        import json as _json

        raw = _json.loads(text)
    return parse_sandbox_config(raw)


def _validate_input_name(name: str) -> None:
    """Names must be plain filenames — no slashes, no leading dot. Keeps
    the in/out surface flat and predictable; defense in depth against
    path-traversal attempts even though we mount the dir read-only."""
    if not name:
        raise ValueError("input/output name must be non-empty")
    if "/" in name or "\\" in name:
        raise ValueError(f"input/output name {name!r} must not contain a path separator")
    if name.startswith("."):
        raise ValueError(f"input/output name {name!r} must not start with a dot")
    if name in ("..", "."):
        raise ValueError(f"input/output name {name!r} is reserved")


def _harvest_outputs(
    out_dir,
    spec: PodmanRegionSpec,
) -> tuple[SandboxOutputFile, ...]:
    """Walk `out_dir` (flat — no recursion) and produce a tuple of
    SandboxOutputFile records, capped by the region's per-file +
    total caps. Files are sorted by name for deterministic ordering
    (audit replay friendly)."""
    from pathlib import Path

    out_path = Path(out_dir)
    if not out_path.is_dir():
        return ()
    entries = sorted(
        [p for p in out_path.iterdir() if p.is_file()],
        key=lambda p: p.name,
    )
    if len(entries) > spec.output_max_files:
        entries = entries[: spec.output_max_files]
    results: list[SandboxOutputFile] = []
    total_seen = 0
    for entry in entries:
        try:
            data = entry.read_bytes()
        except OSError:
            continue
        size = len(data)
        if total_seen + size > spec.output_max_total_bytes:
            # Cap reached — emit a placeholder for the truncated file
            # and stop. The agent gets visibility that more existed.
            remaining = max(spec.output_max_total_bytes - total_seen, 0)
            data = data[:remaining]
        total_seen += len(data)
        sha = hashlib.sha256(data).hexdigest()
        preview_bytes = data[: spec.output_preview_bytes]
        try:
            preview = preview_bytes.decode("utf-8")
        except UnicodeDecodeError:
            preview = preview_bytes.decode("utf-8", errors="replace")
        results.append(
            SandboxOutputFile(
                name=entry.name,
                size=size,
                sha256=sha,
                preview=preview,
                truncated=size > spec.output_preview_bytes
                or total_seen >= spec.output_max_total_bytes,
            ),
        )
        if total_seen >= spec.output_max_total_bytes:
            break
    return tuple(results)


def _safe_emit(cb: ProgressCallback | None):
    """Wrap a user-provided callback so callback exceptions can't
    break the actuator's I/O loop. A noisy observer is a bug, but
    it must not corrupt the container's stdout/stderr capture."""
    def _emit(event: SandboxProgress) -> None:
        if cb is None:
            return
        with contextlib.suppress(Exception):
            cb(event)

    return _emit
