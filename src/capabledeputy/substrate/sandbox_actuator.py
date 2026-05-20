"""Sandbox actuator port — interface only (003 US6 T085 / FR-040/042).

The `SandboxActuator` interface that `EXECUTE.sandbox`-class effects
require for autonomous execution. Provider impls (e.g., gVisor,
Firecracker, dedicated container runtime) live in spec 004.

Per Constitution Principle VI fail-closed: invoking
`EXECUTE.sandbox` when no SandboxActuator is wired MUST refuse with
`OverrideRequired` (T084). This stub is enough to type-check the
port and refuse explicitly at first call.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class SandboxResult:
    """The minimal record a sandbox return must surface.
    `region_id` is the disposable region the run happened in; the
    isolation-posture rules (T084) consult it to compose effective
    reversibility."""

    region_id: str
    exit_code: int
    output_digest: str  # hash of any output that left the region


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
    ) -> SandboxResult:
        """Run `argv` inside the disposable isolation region
        `region_id` with declared timeout. Network egress MUST be
        refused unless the operator-curated config explicitly allows
        it for this region."""

    @abstractmethod
    def create_region(self) -> str:
        """Allocate a fresh disposable region. Returns its id."""

    @abstractmethod
    def discard_region(self, region_id: str) -> None:
        """Tear down a disposable region (FR-040 — region death
        is the containment guarantee)."""
