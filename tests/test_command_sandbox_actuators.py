"""Modal/Firecracker command-runner SandboxActuator tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from capabledeputy.substrate.command_sandbox import (
    CommandSandboxSpec,
    FirecrackerSandboxActuator,
    ModalSandboxActuator,
)


def _runner(tmp_path: Path) -> Path:
    script = tmp_path / "runner.py"
    script.write_text(
        "\n".join(
            [
                "import json, sys",
                "req = json.loads(sys.stdin.read())",
                "assert req['network']['mode'] == 'none'",
                "print(json.dumps({",
                "  'exit_code': 0,",
                "  'output_digest': 'digest-' + req['provider'],",
                "  'outputs': [{'name': 'request.json', 'size': len(json.dumps(req)),",
                "               'sha256': 'x' * 64, 'preview': req['spec_id'],",
                "               'truncated': False}],",
                "}))",
            ],
        ),
        encoding="utf-8",
    )
    return script


def test_modal_actuator_executes_runner_request(tmp_path: Path) -> None:
    spec = CommandSandboxSpec(
        spec_id="modal-python",
        provider="modal",
        runner=(sys.executable, str(_runner(tmp_path))),
    )
    actuator = ModalSandboxActuator((spec,))
    region_id = actuator.create_region("modal-python")
    result = actuator.execute(
        region_id=region_id,
        argv=("python", "-c", "print(1)"),
        env={},
        timeout_seconds=5,
    )
    assert result.exit_code == 0
    assert result.output_digest == "digest-modal"
    assert result.outputs[0].preview == "modal-python"
    actuator.discard_region(region_id)


def test_firecracker_actuator_executes_runner_request(tmp_path: Path) -> None:
    spec = CommandSandboxSpec(
        spec_id="microvm",
        provider="firecracker",
        runner=(sys.executable, str(_runner(tmp_path))),
    )
    actuator = FirecrackerSandboxActuator((spec,))
    region_id = actuator.create_region("microvm")
    result = actuator.execute(
        region_id=region_id,
        argv=("true",),
        env={},
        timeout_seconds=5,
    )
    assert result.output_digest == "digest-firecracker"


def test_command_sandbox_rejects_unallowlisted_network() -> None:
    with pytest.raises(ValueError, match="allowed_hosts"):
        CommandSandboxSpec(
            spec_id="bad",
            provider="modal",
            runner=("modal-runner",),
            network="bridge",
        ).validate()


def test_provider_actuator_rejects_wrong_provider_spec(tmp_path: Path) -> None:
    spec = CommandSandboxSpec(
        spec_id="wrong",
        provider="firecracker",
        runner=(sys.executable, str(_runner(tmp_path))),
    )
    with pytest.raises(ValueError, match="cannot run"):
        ModalSandboxActuator((spec,))
