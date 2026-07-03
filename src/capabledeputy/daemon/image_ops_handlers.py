"""Daemon-owned local image/model operations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import anyio

from capabledeputy.app import App
from capabledeputy.audit.events import Event, EventType
from capabledeputy.daemon.handlers import Handler
from capabledeputy.daemon.settings_store import load_settings, update_settings
from capabledeputy.mcp_servers._image_pipeline import (
    available_image_profiles,
    generate_image,
    image_readiness,
    load_image_gen_config,
)


@dataclass(frozen=True)
class ImageJob:
    id: str
    profile: str
    prompt: str
    status: str
    created_at: datetime
    updated_at: datetime
    stream: str
    style: str | None = None
    backend: str | None = None
    model: str | None = None
    width: int | None = None
    height: int | None = None
    steps: int | None = None
    cancel_requested: bool = False
    error: str | None = None
    result: dict[str, Any] | None = None

    def to_dict(self, *, include_prompt: bool = False) -> dict[str, Any]:
        elapsed = max(0.0, (self.updated_at - self.created_at).total_seconds())
        data: dict[str, Any] = {
            "id": self.id,
            "profile": self.profile,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "elapsed_seconds": elapsed,
            "stream": self.stream,
            "style": self.style,
            "backend": self.backend,
            "model": self.model,
            "width": self.width,
            "height": self.height,
            "steps": self.steps,
            "cancel_requested": self.cancel_requested,
            "error": self.error,
            "result": self.result,
        }
        if include_prompt:
            data["prompt"] = self.prompt
        return data


class ImageJobManager:
    def __init__(self, app: App) -> None:
        self._app = app
        self._jobs: dict[str, ImageJob] = {}
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._lock = anyio.Lock()

    async def start(self, params: dict[str, Any]) -> dict[str, Any]:
        prompt = str(params.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("prompt is required")
        daemon = getattr(self._app, "daemon_server", None)
        if daemon is None or not hasattr(daemon, "start_background"):
            raise RuntimeError("daemon background task group is not available")
        profile = str(params.get("profile") or load_settings().image_profile).strip().lower()
        config = load_image_gen_config(profile_name=profile)
        now = datetime.now(UTC)
        job = ImageJob(
            id=str(uuid4()),
            profile=profile,
            prompt=prompt,
            status="queued",
            created_at=now,
            updated_at=now,
            stream=f"image:{uuid4()}",
            style=str(params.get("style") or config.default_style).strip() or None,
            backend=config.backend,
            model=config.model,
            width=int(params["width"]) if params.get("width") is not None else config.default_width,
            height=(
                int(params["height"]) if params.get("height") is not None else config.default_height
            ),
            steps=int(params["steps"]) if params.get("steps") is not None else config.default_steps,
        )
        job = replace(job, stream=f"image:{job.id}")
        async with self._lock:
            self._jobs[job.id] = job
            self._events[job.id] = []
        await self._emit(job.id, "queued", {"job": job.to_dict(include_prompt=True)})
        daemon.start_background(self._run, job.id, params)
        return {"job": job.to_dict(include_prompt=True)}

    async def get(self, params: dict[str, Any]) -> dict[str, Any]:
        job_id = str(params["job_id"])
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise RuntimeError(f"image job {job_id} not found")
            return {"job": job.to_dict(include_prompt=True)}

    async def list(self, params: dict[str, Any]) -> dict[str, Any]:
        limit = int(params.get("limit") or 50)
        async with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)
        return {"jobs": [job.to_dict(include_prompt=False) for job in jobs[:limit]]}

    async def cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        job_id = str(params["job_id"])
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise RuntimeError(f"image job {job_id} not found")
            if job.status in {"completed", "failed", "canceled"}:
                return {"job": job.to_dict(include_prompt=True)}
            updated = replace(
                job,
                status="canceling",
                cancel_requested=True,
                updated_at=datetime.now(UTC),
            )
            self._jobs[job_id] = updated
        await self._emit(job_id, "canceling", {"job": updated.to_dict(include_prompt=True)})
        return {"job": updated.to_dict(include_prompt=True)}

    async def events(self, params: dict[str, Any]) -> dict[str, Any]:
        job_id = str(params["job_id"])
        cursor = int(params.get("cursor") or 0)
        limit = int(params.get("limit") or 200)
        async with self._lock:
            events = list(self._events.get(job_id) or [])
        return {
            "events": events[cursor : cursor + limit],
            "next_cursor": min(len(events), cursor + limit),
        }

    async def _run(self, job_id: str, params: dict[str, Any]) -> None:
        await self._set_status(job_id, "loading")
        async with self._lock:
            job = self._jobs[job_id]
        try:
            if job.cancel_requested:
                await self._set_status(job_id, "canceled")
                return
            await self._set_status(job_id, "running")
            result = await anyio.to_thread.run_sync(
                lambda: generate_image(
                    prompt=job.prompt,
                    style=job.style,
                    negative_prompt=str(params.get("negative_prompt") or "").strip() or None,
                    width=job.width,
                    height=job.height,
                    steps=job.steps,
                    seed=int(params["seed"]) if params.get("seed") is not None else None,
                    alt=str(params.get("alt") or "").strip() or None,
                    filename=str(params.get("filename") or "").strip() or None,
                    config=load_image_gen_config(profile_name=job.profile),
                ),
                abandon_on_cancel=False,
            )
            await self._set_status(job_id, "finalizing")
            async with self._lock:
                current = self._jobs[job_id]
                if current.cancel_requested:
                    updated = replace(
                        current,
                        status="canceled",
                        result=result,
                        updated_at=datetime.now(UTC),
                    )
                elif result.get("ok"):
                    updated = replace(
                        current,
                        status="completed",
                        result=result,
                        backend=str(result.get("backend") or current.backend),
                        model=str(result.get("model") or current.model),
                        updated_at=datetime.now(UTC),
                    )
                else:
                    updated = replace(
                        current,
                        status="failed",
                        result=result,
                        error=str(result.get("error") or "image generation failed"),
                        updated_at=datetime.now(UTC),
                    )
                self._jobs[job_id] = updated
            await self._emit(
                updated.id, updated.status, {"job": updated.to_dict(include_prompt=True)}
            )
        except Exception as exc:
            async with self._lock:
                current = self._jobs[job_id]
                updated = replace(
                    current,
                    status="failed",
                    error=str(exc),
                    updated_at=datetime.now(UTC),
                )
                self._jobs[job_id] = updated
            await self._emit(job_id, "failed", {"job": updated.to_dict(include_prompt=True)})

    async def _set_status(self, job_id: str, status: str) -> ImageJob:
        async with self._lock:
            job = self._jobs[job_id]
            updated = replace(job, status=status, updated_at=datetime.now(UTC))
            self._jobs[job_id] = updated
        await self._emit(job_id, status, {"job": updated.to_dict(include_prompt=True)})
        return updated

    async def _emit(self, job_id: str, event_type: str, payload: dict[str, Any]) -> None:
        event = {
            "index": 0,
            "event_type": event_type,
            "payload": payload,
            "created_at": datetime.now(UTC).isoformat(),
        }
        async with self._lock:
            events = self._events.setdefault(job_id, [])
            event["index"] = len(events)
            events.append(event)
        daemon = getattr(self._app, "daemon_server", None)
        if daemon is not None and hasattr(daemon, "publish"):
            await daemon.publish(f"image:{job_id}", event)


def make_image_ops_handlers(app: App) -> dict[str, Handler]:
    manager = ImageJobManager(app)

    async def image_profiles(params: dict[str, Any]) -> dict[str, Any]:
        selected = load_settings().image_profile
        return {
            "selected": selected,
            "profiles": available_image_profiles(),
        }

    async def image_profile_get(params: dict[str, Any]) -> dict[str, Any]:
        selected = load_settings().image_profile
        return {
            "selected": selected,
            "readiness": image_readiness(profile_name=selected),
        }

    async def image_profile_set(params: dict[str, Any]) -> dict[str, Any]:
        profile = str(params["profile"]).strip().lower()
        known = {item["id"] for item in available_image_profiles()}
        if profile not in known:
            raise ValueError(f"unknown image profile {profile!r}")
        settings, changed = update_settings({"image_profile": profile})
        await app.audit.write(
            Event(
                event_type=EventType.SETUP_CHANGED,
                payload={
                    "action": "image.profile.set",
                    "changed": list(changed),
                    "image_profile": settings.image_profile,
                },
            ),
        )
        return {
            "selected": settings.image_profile,
            "changed": list(changed),
            "readiness": image_readiness(profile_name=settings.image_profile),
        }

    async def image_readiness_handler(params: dict[str, Any]) -> dict[str, Any]:
        profile = str(params.get("profile") or load_settings().image_profile).strip().lower()
        return image_readiness(profile_name=profile)

    return {
        "image.profiles": image_profiles,
        "image.profile.get": image_profile_get,
        "image.profile.set": image_profile_set,
        "image.readiness": image_readiness_handler,
        "image.jobs.start": manager.start,
        "image.jobs.get": manager.get,
        "image.jobs.list": manager.list,
        "image.jobs.cancel": manager.cancel,
        "image.jobs.events": manager.events,
    }
