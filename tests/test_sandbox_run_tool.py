"""Tests for the agent-callable `sandbox.run` tool (004 U036)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from capabledeputy.substrate.sandbox_actuator import SandboxOutputFile, SandboxResult
from capabledeputy.tools.native.sandbox import make_sandbox_tools
from capabledeputy.tools.registry import ToolContext


class _FakePolicyContext:
    def __init__(self, actuator) -> None:
        self.sandbox_actuator = actuator


def _make_actuator(
    *,
    create_id: str = "region-xyz",
    exit_code: int = 0,
    outputs: tuple[SandboxOutputFile, ...] = (),
) -> MagicMock:
    actuator = MagicMock()
    actuator.create_region.return_value = create_id
    actuator.execute.return_value = SandboxResult(
        region_id=create_id,
        exit_code=exit_code,
        output_digest="abc123",
        outputs=outputs,
    )
    actuator.discard_region.return_value = None
    return actuator


def test_make_sandbox_tools_returns_empty_when_no_actuator() -> None:
    """No actuator wired ⇒ no tool exposed to the agent. Cleaner than
    a tool that always denies."""
    assert make_sandbox_tools(None) == []
    assert make_sandbox_tools(_FakePolicyContext(None)) == []


def _tool_by_name(name: str, actuator):
    return {tool.name: tool for tool in make_sandbox_tools(_FakePolicyContext(actuator))}[name]


def test_make_sandbox_tools_returns_sandbox_and_code_tools_when_wired() -> None:
    from capabledeputy.policy.capabilities import CapabilityKind

    actuator = _make_actuator()
    tools = make_sandbox_tools(_FakePolicyContext(actuator))
    assert {tool.name for tool in tools} == {"sandbox.run", "code.execute"}
    t = _tool_by_name("sandbox.run", actuator)
    assert t.name == "sandbox.run"
    assert t.effect_class == "EXECUTE.sandbox"
    # capability_kind can be CapabilityKind enum or str
    kind = t.capability_kind
    kind_str = kind.value if isinstance(kind, CapabilityKind) else kind  # type: ignore[union-attr]
    assert kind_str == "EXECUTE_SANDBOX"
    assert t.target_arg == "spec_id"


def _ctx() -> ToolContext:
    from uuid import uuid4

    from capabledeputy.policy.labels import LabelState

    return ToolContext(session_id=uuid4(), label_state=LabelState())


def test_sandbox_run_happy_path() -> None:
    actuator = _make_actuator(
        outputs=(
            SandboxOutputFile(
                name="result.txt",
                size=20,
                sha256="x" * 64,
                preview="hello sandbox\n",
                truncated=False,
            ),
        ),
    )
    tool = make_sandbox_tools(_FakePolicyContext(actuator))[0]
    result = asyncio.run(
        tool.handler(  # type: ignore[arg-type]
            {"spec_id": "scratch", "argv": ["echo", "hello"]},
            _ctx(),
        ),
    )
    assert result.output["exit_code"] == 0
    assert result.output["spec_id"] == "scratch"
    assert len(result.output["outputs"]) == 1
    assert result.output["outputs"][0]["name"] == "result.txt"
    assert result.output["outputs"][0]["preview"] == "hello sandbox\n"
    # Region was created with the requested spec_id
    actuator.create_region.assert_called_once_with(spec_id="scratch")
    # And discarded at the end
    actuator.discard_region.assert_called_once()


def test_sandbox_run_rejects_missing_spec_id() -> None:
    actuator = _make_actuator()
    tool = _tool_by_name("sandbox.run", actuator)
    result = asyncio.run(
        tool.handler({"argv": ["echo", "hi"]}, _ctx()),  # type: ignore[arg-type]
    )
    assert "spec_id is required" in result.output["error"]
    actuator.create_region.assert_not_called()


def test_sandbox_run_rejects_missing_argv() -> None:
    actuator = _make_actuator()
    tool = _tool_by_name("sandbox.run", actuator)
    result = asyncio.run(
        tool.handler({"spec_id": "scratch"}, _ctx()),  # type: ignore[arg-type]
    )
    assert "argv" in result.output["error"]


def test_sandbox_run_rejects_invalid_argv_type() -> None:
    actuator = _make_actuator()
    tool = _tool_by_name("sandbox.run", actuator)
    result = asyncio.run(
        tool.handler({"spec_id": "scratch", "argv": "echo hi"}, _ctx()),  # type: ignore[arg-type]
    )
    assert "argv must be a non-empty list" in result.output["error"]


def test_sandbox_run_clamps_timeout() -> None:
    actuator = _make_actuator()
    tool = _tool_by_name("sandbox.run", actuator)
    result = asyncio.run(
        tool.handler(  # type: ignore[arg-type]
            {"spec_id": "scratch", "argv": ["true"], "timeout_seconds": 9999},
            _ctx(),
        ),
    )
    assert "timeout_seconds must be in" in result.output["error"]
    result = asyncio.run(
        tool.handler(  # type: ignore[arg-type]
            {"spec_id": "scratch", "argv": ["true"], "timeout_seconds": 0},
            _ctx(),
        ),
    )
    assert "timeout_seconds must be in" in result.output["error"]


def test_sandbox_run_inputs_text() -> None:
    actuator = _make_actuator()
    tool = _tool_by_name("sandbox.run", actuator)
    asyncio.run(
        tool.handler(  # type: ignore[arg-type]
            {
                "spec_id": "scratch",
                "argv": ["cat", "/in/x.txt"],
                "inputs": {"x.txt": "hello"},
            },
            _ctx(),
        ),
    )
    kwargs = actuator.execute.call_args.kwargs
    assert kwargs["inputs"] == {"x.txt": b"hello"}


def test_sandbox_run_inputs_base64() -> None:
    actuator = _make_actuator()
    tool = _tool_by_name("sandbox.run", actuator)
    import base64

    payload = b"\x00\x01\x02binary"
    asyncio.run(
        tool.handler(  # type: ignore[arg-type]
            {
                "spec_id": "scratch",
                "argv": ["cat", "/in/x.bin"],
                "inputs": {"x.bin": {"base64": base64.b64encode(payload).decode()}},
            },
            _ctx(),
        ),
    )
    kwargs = actuator.execute.call_args.kwargs
    assert kwargs["inputs"] == {"x.bin": payload}


def test_sandbox_run_inputs_invalid_value() -> None:
    actuator = _make_actuator()
    tool = _tool_by_name("sandbox.run", actuator)
    result = asyncio.run(
        tool.handler(  # type: ignore[arg-type]
            {
                "spec_id": "scratch",
                "argv": ["true"],
                "inputs": {"x.txt": 42},  # int — not allowed
            },
            _ctx(),
        ),
    )
    assert "must be a string or" in result.output["error"]


def test_sandbox_run_passes_stdin() -> None:
    actuator = _make_actuator()
    tool = _tool_by_name("sandbox.run", actuator)
    asyncio.run(
        tool.handler(  # type: ignore[arg-type]
            {
                "spec_id": "scratch",
                "argv": ["cat"],
                "stdin": "piped input\n",
            },
            _ctx(),
        ),
    )
    kwargs = actuator.execute.call_args.kwargs
    assert kwargs["stdin_bytes"] == b"piped input\n"


def test_sandbox_run_discards_region_on_execute_error() -> None:
    """Even if execute() throws, the region must be discarded."""
    actuator = _make_actuator()
    actuator.execute.side_effect = RuntimeError("boom")
    tool = _tool_by_name("sandbox.run", actuator)
    result = asyncio.run(
        tool.handler({"spec_id": "scratch", "argv": ["true"]}, _ctx()),  # type: ignore[arg-type]
    )
    assert "execute failed" in result.output["error"]
    actuator.discard_region.assert_called_once()


def test_sandbox_run_propagates_cancellation_flags() -> None:
    actuator = MagicMock()
    actuator.create_region.return_value = "region-xyz"
    actuator.execute.return_value = SandboxResult(
        region_id="region-xyz",
        exit_code=137,
        output_digest="cancelled-digest",
        cancelled=True,
        timed_out=False,
    )
    tool = make_sandbox_tools(_FakePolicyContext(actuator))[0]
    result = asyncio.run(
        tool.handler({"spec_id": "scratch", "argv": ["sleep", "60"]}, _ctx()),  # type: ignore[arg-type]
    )
    assert result.output["cancelled"] is True
    assert result.output["exit_code"] == 137


def test_code_execute_stages_python_snippet() -> None:
    actuator = _make_actuator()
    tool = _tool_by_name("code.execute", actuator)
    result = asyncio.run(
        tool.handler(  # type: ignore[arg-type]
            {
                "spec_id": "scratch",
                "language": "python",
                "code": "print('ok')",
                "argv": ["--flag"],
            },
            _ctx(),
        ),
    )
    assert result.output["exit_code"] == 0
    kwargs = actuator.execute.call_args.kwargs
    assert kwargs["argv"] == ("python", "/in/main.py", "--flag")
    assert kwargs["inputs"] == {"main.py": b"print('ok')"}


def test_code_execute_rejects_unknown_language() -> None:
    actuator = _make_actuator()
    tool = _tool_by_name("code.execute", actuator)
    result = asyncio.run(
        tool.handler(  # type: ignore[arg-type]
            {"spec_id": "scratch", "language": "ruby", "code": "puts :ok"},
            _ctx(),
        ),
    )
    assert "language must be" in result.output["error"]
    actuator.create_region.assert_not_called()
