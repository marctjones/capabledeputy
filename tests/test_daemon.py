from pathlib import Path

import anyio
import pytest

from capabledeputy.daemon.server import Daemon
from capabledeputy.ipc.client import DaemonClient, DaemonError, DaemonNotRunningError
from tests._socket_helpers import short_socket_path


@pytest.fixture
def socket_path(tmp_path: Path) -> Path:
    return short_socket_path()


async def _wait_for_socket(path: Path, timeout: float = 2.0) -> None:
    deadline = anyio.current_time() + timeout
    while anyio.current_time() < deadline:
        if path.exists():
            try:
                stream = await anyio.connect_unix(str(path))
                await stream.aclose()
                return
            except (FileNotFoundError, ConnectionRefusedError):
                pass
        await anyio.sleep(0.01)
    raise TimeoutError(f"socket {path} did not become available within {timeout}s")


async def test_version_round_trip(socket_path: Path) -> None:
    daemon = Daemon(socket_path)

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(socket_path)

        client = DaemonClient(socket_path)
        result = await client.call("version")
        assert "version" in result
        assert isinstance(result["version"], str)

        await client.call("shutdown")


async def test_ping_round_trip(socket_path: Path) -> None:
    daemon = Daemon(socket_path)

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(socket_path)

        client = DaemonClient(socket_path)
        result = await client.call("ping")
        assert result == {"ok": True}

        await client.call("shutdown")


async def test_method_not_found_returns_error(socket_path: Path) -> None:
    daemon = Daemon(socket_path)

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(socket_path)

        client = DaemonClient(socket_path)
        with pytest.raises(DaemonError, match="method not found"):
            await client.call("nonexistent_method")

        await client.call("shutdown")


async def test_socket_is_owner_only(socket_path: Path) -> None:
    daemon = Daemon(socket_path)

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(socket_path)

        mode = socket_path.stat().st_mode & 0o777
        assert mode == 0o600

        client = DaemonClient(socket_path)
        await client.call("shutdown")


async def test_socket_is_removed_after_shutdown(socket_path: Path) -> None:
    daemon = Daemon(socket_path)

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(socket_path)

        client = DaemonClient(socket_path)
        await client.call("shutdown")

    assert not socket_path.exists()


async def test_daemon_exits_after_idle_client_timeout(socket_path: Path) -> None:
    daemon = Daemon(socket_path, idle_shutdown_seconds=0.05)

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(socket_path)

        client = DaemonClient(socket_path)
        assert await client.call("ping") == {"ok": True}

        with anyio.fail_after(2):
            while socket_path.exists():
                await anyio.sleep(0.01)


async def test_client_raises_when_daemon_not_running(tmp_path: Path) -> None:
    socket_path = short_socket_path("missing.sock")
    client = DaemonClient(socket_path)
    with pytest.raises(DaemonNotRunningError):
        await client.call("ping")


async def test_custom_handler_can_be_registered(socket_path: Path) -> None:
    daemon = Daemon(socket_path)

    async def handle_echo(params: dict) -> dict:
        return {"echo": params.get("message")}

    daemon.register("echo", handle_echo)

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(socket_path)

        client = DaemonClient(socket_path)
        result = await client.call("echo", {"message": "hello"})
        assert result == {"echo": "hello"}

        await client.call("shutdown")
