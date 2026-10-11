"""Image generation jobs over the real daemon socket.

tests/test_image_ops_handlers.py drives the handlers with fakes. These tests run
the same job lifecycle through a live daemon and the real DaemonClient — the path
the native app and CLI use — with the generator faked so no model is loaded.
They pin faithful result reporting (#417): a failed generation is reported as
failed with the generator's own error, never as a success.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio
import pytest

from capabledeputy.ipc.client import DaemonClient
from tests.daemon_integration import running_daemon


@pytest.fixture(autouse=True)
def _isolated_image_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    # With a `.venv-images` the daemon would spawn the real worker subprocess;
    # force the in-process path so the fake generator drives the job anywhere.
    monkeypatch.setattr(
        "capabledeputy.daemon.image_ops_handlers._image_runtime_python", lambda: None
    )


async def _wait_for_terminal(client: DaemonClient, job_id: str) -> dict[str, Any]:
    with anyio.fail_after(10):
        while True:
            job = (await client.call("image.jobs.get", {"job_id": job_id}))["job"]
            if job["status"] in {"completed", "failed", "canceled"}:
                return job
            await anyio.sleep(0.02)


async def test_successful_generation_completes_with_ordered_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "lighthouse.png"

    def fake_generate_image(**kwargs: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "backend": "mflux",
            "model": "z-image-turbo",
            "image_path": str(out),
            "markdown": "![a lighthouse](lighthouse.png)",
        }

    monkeypatch.setattr(
        "capabledeputy.daemon.image_ops_handlers.generate_image", fake_generate_image
    )

    async with running_daemon(tmp_path) as rd:
        started = await rd.client.call("image.jobs.start", {"prompt": "a lighthouse at dusk"})
        assert started["job"]["status"] == "queued"
        job_id = started["job"]["id"]
        job = await _wait_for_terminal(rd.client, job_id)
        events = (await rd.client.call("image.jobs.events", {"job_id": job_id}))["events"]
        listed = (await rd.client.call("image.jobs.list", {}))["jobs"]

    assert job["status"] == "completed"
    assert job["result"]["ok"] is True
    assert job["result"]["image_path"] == str(out)
    kinds = [event["event_type"] for event in events]
    assert kinds[:3] == ["queued", "loading", "running"]
    assert kinds[-1] == "completed"
    assert job_id in {j["id"] for j in listed}


async def test_failed_generation_is_reported_as_failed_with_the_generators_own_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_generate_image(**kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "error": "out of memory while loading the model"}

    monkeypatch.setattr(
        "capabledeputy.daemon.image_ops_handlers.generate_image", fake_generate_image
    )

    async with running_daemon(tmp_path) as rd:
        started = await rd.client.call("image.jobs.start", {"prompt": "anything"})
        job = await _wait_for_terminal(rd.client, started["job"]["id"])

    assert job["status"] == "failed", job
    # Clients display the top-level `error`; it must be the generator's own words,
    # not a generic placeholder, and the raw result must not claim success.
    assert job["error"] == "out of memory while loading the model"
    assert job["result"]["ok"] is False


async def test_an_empty_prompt_is_rejected_before_any_job_exists(tmp_path: Path) -> None:
    async with running_daemon(tmp_path) as rd:
        with pytest.raises(Exception, match="prompt is required"):
            await rd.client.call("image.jobs.start", {"prompt": "   "})
        listed = (await rd.client.call("image.jobs.list", {}))["jobs"]

    assert listed == []
