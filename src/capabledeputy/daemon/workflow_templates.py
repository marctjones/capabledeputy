"""Daemon workflow template catalog — loaded from operator configs/workflows.yaml."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

DEFAULT_FIRST_WORKFLOW_TEMPLATE_ID = "morning-briefing"

_BUILTIN_WORKFLOWS: tuple[dict[str, Any], ...] = (
    {
        "id": "morning-briefing",
        "title": "Morning Briefing",
        "subtitle": "Calendar, inbox, notes, conflicts, and action items.",
        "purpose_handle": "general",
        "prompt": (
            "Prepare my morning briefing: today's calendar conflicts, urgent messages "
            "from the last day, and action items I still owe."
        ),
        "system_image": "sunrise",
        "requires_foreground_review": False,
    },
    {
        "id": "inbox-triage",
        "title": "Inbox Triage",
        "subtitle": "Summarize and classify messages; draft replies without sending.",
        "purpose_handle": "inbox",
        "prompt": (
            "Triage my inbox into Urgent, Needs reply soon, Waiting, and FYI. "
            "Prepare reply drafts only for items that need a response; do not send."
        ),
        "system_image": "tray.full",
        "requires_foreground_review": False,
    },
)


class WorkflowConfigError(ValueError):
    """workflows.yaml is missing required fields or is unparseable."""


def _resolve_configs_dir() -> Path:
    env = os.environ.get("CAPDEP_CONFIGS_DIR")
    if env:
        return Path(env)
    return Path("configs")


def _normalize_workflow(raw: dict[str, Any], index: int) -> dict[str, Any]:
    workflow_id = str(raw.get("id") or "").strip()
    if not workflow_id:
        raise WorkflowConfigError(f"workflows[{index}] missing 'id'")
    title = str(raw.get("title") or workflow_id).strip()
    prompt = str(raw.get("prompt") or "").strip()
    if not prompt:
        raise WorkflowConfigError(f"workflows[{index}] ({workflow_id}) missing 'prompt'")
    normalized: dict[str, Any] = {
        "id": workflow_id,
        "title": title,
        "subtitle": str(raw.get("subtitle") or "").strip(),
        "purpose_handle": str(raw.get("purpose_handle") or "general").strip(),
        "prompt": prompt,
        "system_image": str(raw.get("system_image") or "sparkles").strip(),
        "requires_foreground_review": bool(raw.get("requires_foreground_review", False)),
    }
    guidance = str(raw.get("agent_guidance") or "").strip()
    if guidance:
        normalized["agent_guidance"] = guidance
    return normalized


def _load_workflow_config(path: Path) -> tuple[str, tuple[dict[str, Any], ...]]:
    if not path.is_file():
        return DEFAULT_FIRST_WORKFLOW_TEMPLATE_ID, _BUILTIN_WORKFLOWS
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise WorkflowConfigError(f"unparseable workflows config: {path} — {e}") from e
    if not isinstance(data, dict):
        raise WorkflowConfigError(f"workflows config must be a mapping: {path}")
    raw_workflows = data.get("workflows") or []
    if not isinstance(raw_workflows, list) or not raw_workflows:
        raise WorkflowConfigError(f"'workflows' must be a non-empty list: {path}")
    workflows = tuple(_normalize_workflow(item, i) for i, item in enumerate(raw_workflows))
    first_id = str(data.get("first_workflow_id") or workflows[0]["id"]).strip()
    if not any(workflow["id"] == first_id for workflow in workflows):
        raise WorkflowConfigError(f"first_workflow_id {first_id!r} not found in workflows")
    return first_id, workflows


def _workflow_catalog() -> tuple[str, tuple[dict[str, Any], ...]]:
    path = _resolve_configs_dir() / "workflows.yaml"
    return _load_workflow_config(path)


def workflow_turn_message(template: dict[str, Any]) -> str:
    """User-visible turn text: operator prompt plus optional agent playbook."""
    prompt = str(template.get("prompt") or "").strip()
    guidance = str(template.get("agent_guidance") or "").strip()
    if not guidance:
        return prompt
    if not prompt:
        return guidance
    return f"{prompt}\n\n{guidance}"


def _public_template(template: dict[str, Any]) -> dict[str, Any]:
    public = dict(template)
    public["turn_message"] = workflow_turn_message(public)
    return public


def first_workflow_template_id() -> str:
    first_id, _ = _workflow_catalog()
    return first_id


# Back-compat alias for setup plan and clients that import the constant.
FIRST_WORKFLOW_TEMPLATE_ID = first_workflow_template_id()


def build_workflow_templates() -> dict[str, Any]:
    _, workflows = _workflow_catalog()
    return {"templates": [_public_template(template) for template in workflows]}


def workflow_template_by_id(template_id: str) -> dict[str, Any] | None:
    _, workflows = _workflow_catalog()
    for template in workflows:
        if template["id"] == template_id:
            return _public_template(template)
    return None


def first_workflow_template() -> dict[str, Any]:
    first_id, workflows = _workflow_catalog()
    for template in workflows:
        if template["id"] == first_id:
            return _public_template(template)
    return _public_template(workflows[0])