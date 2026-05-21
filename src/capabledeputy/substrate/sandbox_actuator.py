"""Sandbox actuator port — interface only (003 US6 T085 / FR-040/042).

The `SandboxActuator` interface that `EXECUTE.sandbox`-class effects
require for autonomous execution. Provider impls (gVisor, Podman,
Firecracker, dedicated container runtime) live in spec 004.

Per Constitution Principle VI fail-closed: invoking
`EXECUTE.sandbox` when no SandboxActuator is wired MUST refuse with
`OverrideRequired` (T084). This stub is enough to type-check the
port and refuse explicitly at first call.

Phase B (004 U035) extends the port with optional progress events
and cooperative cancellation. Both are kw-only, default-None, so
existing impls and call-sites that don't care need no changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class SandboxResult:
    """The minimal record a sandbox return must surface.
    `region_id` is the disposable region the run happened in; the
    isolation-posture rules (T084) consult it to compose effective
    reversibility."""

    region_id: str
    exit_code: int
    output_digest: str  # hash of any output that left the region
    cancelled: bool = False  # True if the run was terminated via cancel()
    timed_out: bool = False  # True if the run hit timeout_seconds


@dataclass(frozen=True)
class SandboxProgress:
    """One progress event emitted during `execute()`.

    `kind` distinguishes the channel; `payload` is the line/text;
    `bytes_seen` is the cumulative number of bytes from that channel
    since the run started — useful for UI throttling.

    Lifecycle phases (`kind=="lifecycle"`):
      - `image_check`     : verifying image is local
      - `image_pull`      : pulling image (slow, network)
      - `container_start` : container started running argv
      - `container_exit`  : container exited (payload = exit code)
      - `cancelled`       : container was killed via cancel()
      - `timed_out`       : container was killed because timeout expired
    """

    kind: Literal["stdout", "stderr", "lifecycle"]
    payload: str
    bytes_seen: int = 0


ProgressCallback = Callable[[SandboxProgress], None]


class SandboxActuator(ABC):
    """Port interface. Provider impls in spec 004."""

    @abstractmethod
    def execute(
        self,
        *,
        region_id: str,
        argv: tuple[str, ...],
        env: dict[str, str],
        timeout_seconds: int,
        progress_callback: ProgressCallback | None = None,
        stdin_bytes: bytes | None = None,
    ) -> SandboxResult:
        """Run `argv` inside the disposable isolation region
        `region_id` with declared timeout. Network egress MUST be
        refused unless the operator-curated config explicitly allows
        it for this region.

        `progress_callback`: if provided, called synchronously from the
        actuator's I/O loop on every lifecycle transition + each
        line-buffered stdout/stderr chunk. Callback exceptions are
        swallowed so a misbehaving observer can't break the run.

        `stdin_bytes`: optional payload piped to the container's stdin
        before it starts producing output. Use this to hand the
        container a JSON blob, a script, or a small input file
        without declaring a mount.
        """

    @abstractmethod
    def create_region(self) -> str:
        """Allocate a fresh disposable region. Returns its id."""

    @abstractmethod
    def discard_region(self, region_id: str) -> None:
        """Tear down a disposable region (FR-040 — region death
        is the containment guarantee). Best-effort kills any
        still-running execution in the region."""

    @abstractmethod
    def cancel(self, region_id: str) -> bool:
        """Request cancellation of any execution currently running in
        `region_id`. Returns True if a running execution was found
        and killed, False if there was nothing to cancel (already
        finished, never started, or unknown region).

        This is cooperative: it does NOT discard the region — that's
        a separate concern (the region may be reused for a follow-up
        execution). For "kill everything and forget about this
        region" use `discard_region(region_id)`.
        """
