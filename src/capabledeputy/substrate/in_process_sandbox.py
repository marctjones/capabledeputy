"""In-process demo SandboxActuator — DEMO/TEST ONLY (003 US6).

A trivial in-process SandboxActuator that satisfies the SandboxActuator
port for demos and CI. It does NOT provide actual isolation — there is
no jailed namespace, no resource limit, no syscall filter. The
behavior is enough to:

  - Demonstrate the FR-040 disposable-region lifecycle
    (create → execute → discard) at the policy layer.
  - Let `EXECUTE.sandbox` effects resolve to ALLOW under operator
    intent rather than refusing with OVERRIDE_REQUIRED.
  - Exercise the isolation_posture composition (FR-040/041) end-to-
    end so a demo can show "containment lifts reversibility to
    reversible/system" without depending on spec 004.

For any real-world deployment, the operator MUST replace this with
a production SandboxActuator (e.g., gVisor, Firecracker, Wasm,
containerd). The class name and module location make the demo-only
nature explicit; CI tests assert that production tools using
`EXECUTE.sandbox` are not wired against this stub.
"""

from __future__ import annotations

import hashlib
import secrets

from capabledeputy.substrate.sandbox_actuator import SandboxActuator, SandboxResult


class InProcessSandboxActuator(SandboxActuator):
    """Demo implementation. Tracks created/discarded region ids so
    tests can verify the lifecycle. NEVER provides real isolation."""

    def __init__(self) -> None:
        self._live_regions: set[str] = set()
        self._discarded_regions: set[str] = set()

    def create_region(self) -> str:
        region_id = f"in-process-region-{secrets.token_hex(8)}"
        self._live_regions.add(region_id)
        return region_id

    def discard_region(self, region_id: str) -> None:
        # FR-040 region death IS the containment guarantee. In this
        # demo there's nothing to actually tear down — but recording
        # the discard lets tests assert the lifecycle.
        if region_id in self._live_regions:
            self._live_regions.discard(region_id)
            self._discarded_regions.add(region_id)

    def execute(
        self,
        *,
        region_id: str,
        argv: tuple[str, ...],
        env: dict[str, str],
        timeout_seconds: int,
    ) -> SandboxResult:
        """Demo execution: refuses if the region isn't live; otherwise
        returns a SandboxResult with a deterministic output digest
        derived from argv + env so audits can reason about it.
        The argv is NOT actually executed — this is a stub."""
        if region_id not in self._live_regions:
            raise RuntimeError(
                f"sandbox region {region_id!r} is not live "
                f"(either never created or already discarded)",
            )
        # Deterministic digest so audit-replay sees stable output ids.
        payload = ("\x00".join(argv) + "\x01" + repr(sorted(env.items()))).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        return SandboxResult(
            region_id=region_id,
            exit_code=0,
            output_digest=digest,
        )

    @property
    def live_regions(self) -> frozenset[str]:
        return frozenset(self._live_regions)

    @property
    def discarded_regions(self) -> frozenset[str]:
        return frozenset(self._discarded_regions)


def is_demo_actuator(actuator: SandboxActuator) -> bool:
    """Returns True for the demo stub. Production CI should refuse
    to deploy with a demo actuator wired."""
    return isinstance(actuator, InProcessSandboxActuator)
