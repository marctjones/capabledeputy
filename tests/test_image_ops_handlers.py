# Vars bound inside `async with create_task_group()` and used after the block
# are safe here (normal completion binds them); silence the false positive.
# pyright: reportPossiblyUnboundVariable=false
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import anyio
import anyio.abc
import pytest

from capabledeputy.audit.events import Event
from capabledeputy.daemon.image_ops_handlers import make_image_ops_handlers


@dataclass
class FakeAudit:
    events: list[Event] = field(default_factory=list)

    async def write(self, event: Event) -> None:
        self.events.append(event)


class FakeDaemon:
    def __init__(self, tg: anyio.abc.TaskGroup) -> None:
        self._tg = tg
        self.published: list[tuple[str, dict[str, Any]]] = []

    def start_background(self, func: object, *args: object) -> None:
        self._tg.start_soon(func, *args)  # type: ignore[arg-type]

    async def publish(self, stream: str, payload: dict[str, Any]) -> None:
        self.published.append((stream, payload))


@dataclass
class FakeApp:
    daemon_server: FakeDaemon | None = None
    audit: FakeAudit = field(default_factory=FakeAudit)


async def test_image_profile_handlers_persist_and_report_readiness(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    app = FakeApp()
    handlers = make_image_ops_handlers(app)  # type: ignore[arg-type]

    profiles = await handlers["image.profiles"]({})
    assert profiles["selected"] == "default"
    assert {profile["id"] for profile in profiles["profiles"]} >= {
        "default",
        "fast",
        "balanced",
        "quality",
    }

    updated = await handlers["image.profile.set"]({"profile": "balanced"})

    assert updated["selected"] == "balanced"
    assert updated["changed"] == ["image_profile"]
    assert updated["readiness"]["profile"] == "balanced"
    assert app.audit.events[-1].payload["action"] == "image.profile.set"


async def test_image_readiness_prefers_isolated_image_runtime(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(
        "capabledeputy.daemon.image_ops_handlers._image_runtime_readiness",
        lambda profile: {
            "ok": True,
            "profile": profile,
            "backend": "mflux",
            "checks": [{"id": "mlx-metal", "status": "ok"}],
            "runtime_python": "/repo/.venv-images/bin/python",
        },
    )
    app = FakeApp()
    handlers = make_image_ops_handlers(app)  # type: ignore[arg-type]

    readiness = await handlers["image.readiness"]({"profile": "default"})

    assert readiness["ok"] is True
    assert readiness["runtime_python"].endswith(".venv-images/bin/python")
    assert readiness["checks"] == [{"id": "mlx-metal", "status": "ok"}]


async def test_image_job_lifecycle_emits_status_events(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    def fake_generate_image(**kwargs: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "backend": "mflux",
            "model": "z-image-turbo",
            "image_path": str(tmp_path / "out.png"),
            "markdown": "![generated](out.png)",
        }

    monkeypatch.setattr(
        "capabledeputy.daemon.image_ops_handlers.generate_image",
        fake_generate_image,
    )

    async with anyio.create_task_group() as tg:
        daemon = FakeDaemon(tg)
        app = FakeApp(daemon_server=daemon)
        handlers = make_image_ops_handlers(app)  # type: ignore[arg-type]

        started = await handlers["image.jobs.start"]({"prompt": "a small test image"})
        job_id = started["job"]["id"]
        assert started["job"]["status"] == "queued"

        with anyio.fail_after(2):
            while True:
                current = await handlers["image.jobs.get"]({"job_id": job_id})
                if current["job"]["status"] == "completed":
                    break
                await anyio.sleep(0.01)
        tg.cancel_scope.cancel()

    assert current["job"]["result"]["ok"] is True
    assert current["job"]["backend"] == "mflux"
    events = await handlers["image.jobs.events"]({"job_id": job_id})
    statuses = [event["event_type"] for event in events["events"]]
    assert statuses[:3] == ["queued", "loading", "running"]
    assert statuses[-2:] == ["finalizing", "completed"]
    assert any(stream == f"image:{job_id}" for stream, _payload in daemon.published)


async def test_image_job_start_fails_before_queue_when_background_unavailable(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    handlers = make_image_ops_handlers(FakeApp())  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="background task group"):
        await handlers["image.jobs.start"]({"prompt": "will not queue"})

    listed = await handlers["image.jobs.list"]({})
    assert listed["jobs"] == []


async def test_image_job_cancel_marks_completed_generation_canceled(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    def slow_generate_image(**kwargs: Any) -> dict[str, Any]:
        import time

        time.sleep(0.08)
        return {
            "ok": True,
            "backend": "mflux",
            "model": "z-image-turbo",
            "image_path": str(tmp_path / "canceled.png"),
            "markdown": "![generated](canceled.png)",
        }

    monkeypatch.setattr(
        "capabledeputy.daemon.image_ops_handlers.generate_image",
        slow_generate_image,
    )

    async with anyio.create_task_group() as tg:
        daemon = FakeDaemon(tg)
        app = FakeApp(daemon_server=daemon)
        handlers = make_image_ops_handlers(app)  # type: ignore[arg-type]

        started = await handlers["image.jobs.start"]({"prompt": "cancel this image"})
        job_id = started["job"]["id"]

        with anyio.fail_after(2):
            while True:
                current = await handlers["image.jobs.get"]({"job_id": job_id})
                if current["job"]["status"] == "running":
                    break
                await anyio.sleep(0.01)

        cancel_result = await handlers["image.jobs.cancel"]({"job_id": job_id})
        assert cancel_result["job"]["status"] == "canceling"
        assert cancel_result["job"]["cancel_requested"] is True

        with anyio.fail_after(2):
            while True:
                current = await handlers["image.jobs.get"]({"job_id": job_id})
                if current["job"]["status"] == "canceled":
                    break
                await anyio.sleep(0.01)
        tg.cancel_scope.cancel()

    assert current["job"]["cancel_requested"] is True
    assert current["job"]["result"]["ok"] is True
    events = await handlers["image.jobs.events"]({"job_id": job_id})
    statuses = [event["event_type"] for event in events["events"]]
    assert "canceling" in statuses
    assert statuses[-1] == "canceled"


async def test_image_job_failed_result_surfaces_actionable_error(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    def fake_generate_image(**kwargs: Any) -> dict[str, Any]:
        return {
            "ok": False,
            "backend": "mflux",
            "model": "z-image-turbo",
            "error": "model weights missing",
        }

    monkeypatch.setattr(
        "capabledeputy.daemon.image_ops_handlers.generate_image",
        fake_generate_image,
    )

    async with anyio.create_task_group() as tg:
        daemon = FakeDaemon(tg)
        app = FakeApp(daemon_server=daemon)
        handlers = make_image_ops_handlers(app)  # type: ignore[arg-type]

        started = await handlers["image.jobs.start"]({"prompt": "will fail"})
        job_id = started["job"]["id"]

        with anyio.fail_after(2):
            while True:
                current = await handlers["image.jobs.get"]({"job_id": job_id})
                if current["job"]["status"] == "failed":
                    break
                await anyio.sleep(0.01)
        tg.cancel_scope.cancel()

    assert current["job"]["error"] == "model weights missing"
    assert current["job"]["result"]["ok"] is False
    events = await handlers["image.jobs.events"]({"job_id": job_id})
    statuses = [event["event_type"] for event in events["events"]]
    assert statuses[-1] == "failed"
