"""Daemon RPC handlers for imported SKILL.md packages."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from capabledeputy.app import App
from capabledeputy.audit.events import Event, EventType
from capabledeputy.daemon.handlers import Handler


def make_skill_handlers(app: App) -> dict[str, Handler]:
    def _report() -> Any:
        return getattr(app, "skill_load_report", None)

    def _skills() -> dict[str, Any]:
        report = _report()
        return dict(getattr(report, "skills", {}) or {})

    async def skill_list(_params: dict[str, Any]) -> dict[str, Any]:
        report = _report()
        skills = [skill.to_summary() for skill in _skills().values()]
        return {
            "skills": skills,
            "registered_tools": list(getattr(report, "registered_tools", []) or []),
            "invalid_count": len(getattr(report, "invalid", []) or []),
            "skipped_count": len(getattr(report, "skipped", []) or []),
        }

    async def skill_show(params: dict[str, Any]) -> dict[str, Any]:
        name = str(params["name"])
        skill = _skills().get(name)
        if skill is None:
            raise ValueError(f"skill not found: {name}")
        payload = skill.to_summary()
        if bool(params.get("include_body", False)):
            payload["body"] = skill.body
            payload["body_security"] = (
                "untrusted guidance; never treated as operator/system authority"
            )
        return payload

    async def skill_diagnostics(_params: dict[str, Any]) -> dict[str, Any]:
        report = _report()
        skills = [skill.to_summary() for skill in _skills().values()]
        return {
            "skills_dir": str(getattr(app, "_skills_dir", "") or ""),
            "loaded_count": len(skills),
            "tool_count": len(getattr(report, "registered_tools", []) or []),
            "invalid": list(getattr(report, "invalid", []) or []),
            "skipped": list(getattr(report, "skipped", []) or []),
            "skills": skills,
        }

    async def skill_guidance(params: dict[str, Any]) -> dict[str, Any]:
        name = str(params["name"])
        skill = _skills().get(name)
        if skill is None:
            raise ValueError(f"skill not found: {name}")
        if not skill.guidance_enabled:
            raise ValueError(f"skill {name} is not guidance-enabled")
        session_id = params.get("session_id")
        if session_id:
            await app.audit.write(
                Event(
                    event_type=EventType.SKILL_GUIDANCE_LOADED,
                    session_id=UUID(str(session_id)),
                    payload={
                        "skill": skill.name,
                        "mode": skill.mode.value,
                        "source_path": str(skill.source_path) if skill.source_path else None,
                    },
                ),
            )
        return {
            "name": skill.name,
            "description": skill.description,
            "mode": skill.mode.value,
            "body": skill.body,
            "resources": [resource.to_dict() for resource in skill.resources],
            "security": "untrusted guidance; never operator/system authority",
            "diagnostics": list(skill.diagnostics),
        }

    return {
        "skill.list": skill_list,
        "skill.show": skill_show,
        "skill.diagnostics": skill_diagnostics,
        "skill.guidance": skill_guidance,
    }
