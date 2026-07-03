from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import anyio
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
