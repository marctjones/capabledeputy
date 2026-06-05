"""Unit tests for the devbox.* tool family (tools/native/devbox.py).

A fake PodmanDevbox stand-in records calls so tests assert on the
tool's argument handling, parameter validation, and result shaping
WITHOUT depending on real Podman. The substrate layer is covered
separately by test_podman_devbox.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest

from capabledeputy.tools.native.devbox import make_devbox_tools
from capabledeputy.tools.registry import ToolContext


@dataclass
class _FakeLive:
    container_name: str
    workspace_host_path: str


@dataclass
class _FakeExecResult:
    container_name: str
    exit_code: int
    stdout: bytes
    stderr: bytes
    cancelled: bool = False
    timed_out: bool = False


class FakeDevbox:
    """Records every call. Tests assert on `start_calls`, `exec_calls`,
    `stop_calls`. Each method returns a canned response unless
    `raise_on_<method>` is set."""

    def __init__(self) -> None:
        self.start_calls: list[tuple[UUID, str]] = []
        self.exec_calls: list[dict[str, Any]] = []
        self.stop_calls: list[tuple[UUID, str, bool]] = []
        self.list_session_calls: list[UUID] = []
        self.list_specs_calls: int = 0
        self.exec_response = _FakeExecResult(
            container_name="capdep-devbox-test",
            exit_code=0,
            stdout=b"ok\n",
            stderr=b"",
        )
        self.live_response = _FakeLive(
            container_name="capdep-devbox-test",
            workspace_host_path="/tmp/ws/test",
        )

    def start_or_get(self, session_id: UUID, spec_id: str) -> _FakeLive:
        self.start_calls.append((session_id, spec_id))
        return self.live_response

    def exec(self, session_id: UUID, spec_id: str, **kw) -> _FakeExecResult:
        self.exec_calls.append({"session_id": session_id, "spec_id": spec_id, **kw})
        return self.exec_response

    def stop(self, session_id: UUID, spec_id: str, *, purge_workspace: bool = False) -> bool:
        self.stop_calls.append((session_id, spec_id, purge_workspace))
        return True

    def list_session(self, session_id: UUID):
        self.list_session_calls.append(session_id)
        return ({"spec_id": "dev", "container_name": "x", "is_alive": True},)

    def list_specs(self):
        self.list_specs_calls += 1
        return ("dev", "py")


class _FakePolicyCtx:
    def __init__(self, devbox_manager: Any) -> None:
        self.devbox_manager = devbox_manager


def _ctx(session_id: UUID | None = None) -> ToolContext:
    return ToolContext(session_id=session_id or uuid4(), label_set=frozenset())


def _tool_by_name(tools, name: str):
    for t in tools:
        if t.name == name:
            return t
    raise AssertionError(f"tool {name!r} not found in {[t.name for t in tools]}")


# --- Discovery -----------------------------------------------------------


def test_make_devbox_tools_empty_without_manager() -> None:
    """No PodmanDevbox wired → no tools registered. Mirrors
    sandbox.run's behavior: better an empty tool list than a tool
    that always returns 'not configured'."""
    assert make_devbox_tools(None) == []
    assert make_devbox_tools(_FakePolicyCtx(devbox_manager=None)) == []


def test_make_devbox_tools_returns_four_when_manager_wired() -> None:
    tools = make_devbox_tools(_FakePolicyCtx(devbox_manager=FakeDevbox()))
    names = sorted(t.name for t in tools)
    assert names == ["devbox.exec", "devbox.list", "devbox.start", "devbox.stop"]


# --- devbox.start --------------------------------------------------------


@pytest.mark.anyio
async def test_start_requires_spec_id() -> None:
    fake = FakeDevbox()
    start = _tool_by_name(make_devbox_tools(_FakePolicyCtx(fake)), "devbox.start")
    result = await start.handler({}, _ctx())
    assert "error" in result.output
    assert "spec_id" in result.output["error"]
    assert fake.start_calls == []


@pytest.mark.anyio
async def test_start_returns_container_name_and_workspace() -> None:
    fake = FakeDevbox()
    start = _tool_by_name(make_devbox_tools(_FakePolicyCtx(fake)), "devbox.start")
    sid = uuid4()
    result = await start.handler({"spec_id": "dev"}, _ctx(sid))
    assert result.output["started"] is True
    assert result.output["container_name"] == "capdep-devbox-test"
    assert result.output["workspace_host_path"] == "/tmp/ws/test"
    assert fake.start_calls == [(sid, "dev")]


# --- devbox.exec ---------------------------------------------------------


@pytest.mark.anyio
async def test_exec_validates_argv_shape() -> None:
    fake = FakeDevbox()
    exec_tool = _tool_by_name(make_devbox_tools(_FakePolicyCtx(fake)), "devbox.exec")
    # missing argv
    result = await exec_tool.handler({"spec_id": "dev"}, _ctx())
    assert "argv must be a non-empty list" in result.output["error"]
    # empty argv
    result = await exec_tool.handler({"spec_id": "dev", "argv": []}, _ctx())
    assert "argv must be a non-empty list" in result.output["error"]


@pytest.mark.anyio
async def test_exec_clamps_timeout_range() -> None:
    fake = FakeDevbox()
    exec_tool = _tool_by_name(make_devbox_tools(_FakePolicyCtx(fake)), "devbox.exec")
    result = await exec_tool.handler(
        {"spec_id": "dev", "argv": ["true"], "timeout_seconds": 99999},
        _ctx(),
    )
    assert "timeout_seconds" in result.output["error"]
    assert fake.exec_calls == []


@pytest.mark.anyio
async def test_exec_passes_through_to_manager() -> None:
    fake = FakeDevbox()
    exec_tool = _tool_by_name(make_devbox_tools(_FakePolicyCtx(fake)), "devbox.exec")
    sid = uuid4()
    result = await exec_tool.handler(
        {
            "spec_id": "dev",
            "argv": ["python", "-c", "print(1+1)"],
            "timeout_seconds": 10,
            "workdir": "/work/src",
            "env": {"PYTHONUNBUFFERED": "1"},
            "stdin": "hello\n",
        },
        _ctx(sid),
    )
    assert result.output["exit_code"] == 0
    assert result.output["stdout"] == "ok\n"
    assert result.output["spec_id"] == "dev"
    [call] = fake.exec_calls
    assert call["session_id"] == sid
    assert call["spec_id"] == "dev"
    assert call["argv"] == ("python", "-c", "print(1+1)")
    assert call["timeout_seconds"] == 10
    assert call["workdir"] == "/work/src"
    assert call["env"] == {"PYTHONUNBUFFERED": "1"}
    assert call["stdin_bytes"] == b"hello\n"


@pytest.mark.anyio
async def test_exec_base64_stdin() -> None:
    fake = FakeDevbox()
    exec_tool = _tool_by_name(make_devbox_tools(_FakePolicyCtx(fake)), "devbox.exec")
    result = await exec_tool.handler(
        {
            "spec_id": "dev",
            "argv": ["base64", "-d"],
            "stdin": {"base64": "aGVsbG8="},  # "hello"
        },
        _ctx(),
    )
    assert "error" not in result.output
    [call] = fake.exec_calls
    assert call["stdin_bytes"] == b"hello"


@pytest.mark.anyio
async def test_exec_truncates_huge_stdout() -> None:
    fake = FakeDevbox()
    fake.exec_response = _FakeExecResult(
        container_name="x",
        exit_code=0,
        stdout=b"A" * 30_000,
        stderr=b"",
    )
    exec_tool = _tool_by_name(make_devbox_tools(_FakePolicyCtx(fake)), "devbox.exec")
    result = await exec_tool.handler(
        {"spec_id": "dev", "argv": ["yes"]},
        _ctx(),
    )
    assert result.output["stdout_truncated"] is True
    assert "truncated" in result.output["stdout"]
    # Capped at 16 KiB + truncation marker
    assert len(result.output["stdout"].encode("utf-8")) < 30_000


# --- devbox.stop ---------------------------------------------------------


@pytest.mark.anyio
async def test_stop_default_preserves_workspace() -> None:
    fake = FakeDevbox()
    stop = _tool_by_name(make_devbox_tools(_FakePolicyCtx(fake)), "devbox.stop")
    sid = uuid4()
    result = await stop.handler({"spec_id": "dev"}, _ctx(sid))
    assert result.output["stopped"] is True
    assert result.output["workspace_purged"] is False
    assert fake.stop_calls == [(sid, "dev", False)]


@pytest.mark.anyio
async def test_stop_purge_workspace_threads_through() -> None:
    fake = FakeDevbox()
    stop = _tool_by_name(make_devbox_tools(_FakePolicyCtx(fake)), "devbox.stop")
    sid = uuid4()
    result = await stop.handler(
        {"spec_id": "dev", "purge_workspace": True},
        _ctx(sid),
    )
    assert result.output["workspace_purged"] is True
    assert fake.stop_calls == [(sid, "dev", True)]


# --- devbox.list ---------------------------------------------------------


@pytest.mark.anyio
async def test_list_returns_live_and_available() -> None:
    fake = FakeDevbox()
    listt = _tool_by_name(make_devbox_tools(_FakePolicyCtx(fake)), "devbox.list")
    sid = uuid4()
    result = await listt.handler({}, _ctx(sid))
    assert result.output["available_specs"] == ["dev", "py"]
    assert result.output["live"][0]["spec_id"] == "dev"
    assert fake.list_session_calls == [sid]
    assert fake.list_specs_calls == 1


# --- Capability + effect_class wiring ------------------------------------


def test_all_devbox_tools_route_through_execute_devbox_kind() -> None:
    """Every devbox tool MUST require EXECUTE_DEVBOX. Drift here is a
    real bug: a tool with the wrong capability kind would bypass the
    operator's grant scoping."""
    from capabledeputy.policy.capabilities import CapabilityKind

    tools = make_devbox_tools(_FakePolicyCtx(FakeDevbox()))
    for t in tools:
        assert t.capability_kind == CapabilityKind.EXECUTE_DEVBOX, (
            f"{t.name} uses {t.capability_kind}; expected EXECUTE_DEVBOX"
        )
        assert t.effect_class == "EXECUTE.devbox"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
