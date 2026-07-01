"""Command-runner SandboxActuators for heavier isolation providers.

Modal and Firecracker have very different control planes, but both can
be safely integrated behind the same in-repo ``SandboxActuator`` port by
delegating one bounded execution request to an operator-owned runner.
The runner receives a deterministic JSON request on stdin and returns a
small JSON result on stdout. Provider credentials, kernel images, VM
networking, and cloud-specific details stay outside the TCB; CapDep
verifies the request shape and preserves the same fail-closed semantics
as the Podman actuator.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import secrets
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Literal

from capabledeputy.substrate.sandbox_actuator import (
    ProgressCallback,
    SandboxActuator,
    SandboxOutputFile,
    SandboxProgress,
    SandboxResult,
)


class SandboxProviderUnavailableError(RuntimeError):
    """Selected provider runner is not installed or failed its probe."""


class SandboxProviderError(RuntimeError):
    """Provider runner returned malformed output or a non-zero exit."""


@dataclass(frozen=True)
class CommandSandboxSpec:
    spec_id: str
    provider: Literal["modal", "firecracker"]
    runner: tuple[str, ...]
    network: str = "none"
    allowed_hosts: tuple[str, ...] = ()
    env_allowlist: tuple[str, ...] = ()
    timeout_seconds_default: int = 30

    def validate(self) -> None:
        if not self.spec_id:
            raise ValueError("command sandbox spec_id is required")
        if not self.runner:
            raise ValueError(f"{self.spec_id}: runner argv is required")
        if self.network == "host":
            raise ValueError(f"{self.spec_id}: host networking is not allowed")
        if self.network != "none" and not self.allowed_hosts:
            raise ValueError(
                f"{self.spec_id}: network={self.network!r} requires allowed_hosts",
            )
        if self.timeout_seconds_default < 1:
            raise ValueError(f"{self.spec_id}: timeout_seconds_default must be positive")


class CommandSandboxActuator(SandboxActuator):
    """SandboxActuator backed by an external JSON runner command."""

    provider_name = "command"

    def __init__(
        self,
        specs: tuple[CommandSandboxSpec, ...],
        *,
        verify_runner: bool = True,
    ) -> None:
        for spec in specs:
            spec.validate()
            if spec.provider != self.provider_name:
                raise ValueError(
                    f"{self.provider_name} actuator cannot run {spec.provider!r} spec "
                    f"{spec.spec_id!r}",
                )
        self._specs = {spec.spec_id: spec for spec in specs}
        if len(self._specs) != len(specs):
            raise ValueError("duplicate command sandbox spec_id")
        if verify_runner:
            for spec in specs:
                if shutil.which(spec.runner[0]) is None and "/" not in spec.runner[0]:
                    raise SandboxProviderUnavailableError(
                        f"{self.provider_name} runner {spec.runner[0]!r} not found on PATH",
                    )
        self._regions: dict[str, CommandSandboxSpec] = {}

    def create_region(self, spec_id: str | None = None) -> str:  # type: ignore[override]
        if spec_id is None:
            if len(self._specs) != 1:
                raise ValueError(
                    f"create_region() with no spec_id requires exactly one registered spec; "
                    f"have {sorted(self._specs)!r}",
                )
            spec_id = next(iter(self._specs))
        spec = self._specs.get(spec_id)
        if spec is None:
            raise KeyError(f"unknown {self.provider_name} sandbox spec {spec_id!r}")
        region_id = f"{self.provider_name}-{spec_id}-{secrets.token_hex(8)}"
        self._regions[region_id] = spec
        return region_id

    def discard_region(self, region_id: str) -> None:
        self._regions.pop(region_id, None)

    def cancel(self, region_id: str) -> bool:
        # Runner executions are synchronous subprocesses. Long-running
        # cancellation is delegated to the runner's own timeout handling.
        return region_id in self._regions

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
        spec = self._regions.get(region_id)
        if spec is None:
            raise KeyError(f"sandbox region {region_id!r} is not live")
        emit = _safe_emit(progress_callback)
        emit(SandboxProgress(kind="lifecycle", payload=f"{spec.provider}:runner_start"))
        request = {
            "provider": spec.provider,
            "region_id": region_id,
            "spec_id": spec.spec_id,
            "argv": list(argv),
            "env": {name: env[name] for name in spec.env_allowlist if name in env},
            "timeout_seconds": timeout_seconds or spec.timeout_seconds_default,
            "network": {
                "mode": spec.network,
                "allowed_hosts": list(spec.allowed_hosts),
            },
            "stdin_b64": (
                base64.b64encode(stdin_bytes).decode("ascii") if stdin_bytes is not None else None
            ),
            "inputs_b64": {
                name: base64.b64encode(content).decode("ascii")
                for name, content in (inputs or {}).items()
            },
        }
        proc = subprocess.run(
            list(spec.runner),
            input=json.dumps(request, sort_keys=True).encode("utf-8"),
            capture_output=True,
            timeout=timeout_seconds or spec.timeout_seconds_default,
            check=False,
        )
        if proc.returncode != 0:
            raise SandboxProviderError(
                f"{spec.provider} runner exited {proc.returncode}: "
                f"{proc.stderr.decode('utf-8', 'replace')[:300]}",
            )
        try:
            payload = json.loads(proc.stdout.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise SandboxProviderError(f"{spec.provider} runner returned invalid JSON") from e
        emit(SandboxProgress(kind="lifecycle", payload=f"{spec.provider}:runner_exit"))
        return _result_from_payload(region_id, payload)


class ModalSandboxActuator(CommandSandboxActuator):
    provider_name = "modal"


class FirecrackerSandboxActuator(CommandSandboxActuator):
    provider_name = "firecracker"


def _result_from_payload(region_id: str, payload: dict[str, Any]) -> SandboxResult:
    outputs: list[SandboxOutputFile] = []
    for raw in payload.get("outputs") or []:
        if not isinstance(raw, dict):
            continue
        outputs.append(
            SandboxOutputFile(
                name=str(raw.get("name") or ""),
                size=int(raw.get("size") or 0),
                sha256=str(raw.get("sha256") or ""),
                preview=str(raw.get("preview") or ""),
                truncated=bool(raw.get("truncated", False)),
            ),
        )
    digest_input = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return SandboxResult(
        region_id=region_id,
        exit_code=int(payload.get("exit_code", 0)),
        output_digest=str(payload.get("output_digest") or hashlib.sha256(digest_input).hexdigest()),
        cancelled=bool(payload.get("cancelled", False)),
        timed_out=bool(payload.get("timed_out", False)),
        outputs=tuple(outputs),
    )


def _safe_emit(cb: ProgressCallback | None):
    def _emit(event: SandboxProgress) -> None:
        if cb is None:
            return
        with contextlib.suppress(Exception):
            cb(event)

    return _emit
