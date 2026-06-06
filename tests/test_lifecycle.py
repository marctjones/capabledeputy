from pathlib import Path

import anyio

from capabledeputy.daemon.lifecycle import (
    daemon_status,
    run_daemon,
    stop_daemon,
)


async def _wait_for_socket(path: Path, timeout: float = 15.0) -> None:
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


async def test_status_reports_not_running_when_no_daemon(tmp_path: Path) -> None:
    socket_path = tmp_path / "no-daemon.sock"
    status = await daemon_status(socket_path)
    # Issue #1 broadened daemon_status to also report the pid from
    # the pidfile (None when no daemon is running). Don't pin the
    # whole dict shape; just the running flag.
    assert status["running"] is False


async def test_stop_returns_false_when_no_daemon(tmp_path: Path) -> None:
    socket_path = tmp_path / "no-daemon.sock"
    assert await stop_daemon(socket_path) is False


async def test_run_status_stop_lifecycle(tmp_path: Path) -> None:
    socket_path = tmp_path / "lifecycle.sock"

    async with anyio.create_task_group() as tg:
        tg.start_soon(run_daemon, socket_path)
        await _wait_for_socket(socket_path)

        status = await daemon_status(socket_path)
        assert status["running"] is True
        assert status["ping"] == {"ok": True}

        assert await stop_daemon(socket_path) is True
