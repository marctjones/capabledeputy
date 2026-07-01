"""Daemon workflow template catalog — loaded from operator configs/workflows.yaml."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from capabledeputy.policy.capabilities import CapabilityKind

DEFAULT_FIRST_WORKFLOW_TEMPLATE_ID = "morning-briefing"
WORKFLOW_SCHEMA_VERSION = 1

_REQUIRED_SCHEMA_FIELDS: frozenset[str] = frozenset(
    {
        "capabilities",
        "flow_pattern",
        "source_ports",
        "artifact_types",
        "approval_policy",
        "retention",
    },
)
_ALLOWED_FLOW_PATTERNS: frozenset[str] = frozenset(
    {
        "background_read_review",
        "foreground_context_review",
        "foreground_artifact_review",
        "research_synthesis",
    },
)
_ALLOWED_ARTIFACT_TYPES: frozenset[str] = frozenset(
    {
        "email_draft",
        "diff",
        "calendar_event",
        "document",
        "research",
        "image",
        "chart",
    },
)
_ALLOWED_SOURCE_PORTS: frozenset[str] = frozenset(
    {
        "gmail",
        "imap",
        "google-calendar",
        "google-drive",
        "browser.current-page",
        "macos.frontmost-app",
        "apple-mail",
        "finder",
        "pages",
        "numbers",
        "keynote",
        "web",
    },
)
_DEFAULT_APPROVAL_POLICY: dict[str, str] = {
    "mutating_actions": "require_foreground_review",
    "egress": "require_approval",
    "foreground_review": "operator_visible",
}
_DEFAULT_RETENTION: dict[str, str] = {
    "source_context": "session",
    "artifacts": "session",
    "audit": "durable",
}

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
        "capabilities": [
            CapabilityKind.GMAIL_READ.value,
            CapabilityKind.IMAP_READ.value,
            CapabilityKind.CALENDAR_READ.value,
        ],
        "flow_pattern": "background_read_review",
        "source_ports": ["gmail", "imap", "google-calendar"],
        "artifact_types": ["research"],
        "approval_policy": _DEFAULT_APPROVAL_POLICY,
        "retention": _DEFAULT_RETENTION,
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
        "capabilities": [CapabilityKind.GMAIL_READ.value, CapabilityKind.IMAP_READ.value],
        "flow_pattern": "background_read_review",
        "source_ports": ["gmail", "imap"],
        "artifact_types": ["email_draft", "research"],
        "approval_policy": _DEFAULT_APPROVAL_POLICY,
        "retention": _DEFAULT_RETENTION,
    },
)


class WorkflowConfigError(ValueError):
    """workflows.yaml is missing required fields or is unparseable."""


def _resolve_configs_dir() -> Path:
    env = os.environ.get("CAPDEP_CONFIGS_DIR")
    if env:
        return Path(env)
    return Path("configs")


def validate_workflow_manifest(
    raw: dict[str, Any],
    index: int = 0,
    *,
    strict_schema: bool = True,
) -> dict[str, Any]:
    return _normalize_workflow(raw, index, strict_schema=strict_schema)


def _normalize_workflow(
    raw: dict[str, Any],
    index: int,
    *,
    strict_schema: bool = True,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise WorkflowConfigError(f"workflows[{index}] must be a mapping")
    workflow_id = str(raw.get("id") or "").strip()
    if not workflow_id:
        raise WorkflowConfigError(f"workflows[{index}] missing 'id'")
    title = str(raw.get("title") or workflow_id).strip()
    prompt = str(raw.get("prompt") or "").strip()
    if not prompt:
        raise WorkflowConfigError(f"workflows[{index}] ({workflow_id}) missing 'prompt'")
    if strict_schema:
        missing = sorted(field for field in _REQUIRED_SCHEMA_FIELDS if field not in raw)
        if missing:
            raise WorkflowConfigError(
                f"workflows[{index}] ({workflow_id}) missing schema fields: {missing}",
            )
    normalized: dict[str, Any] = {
        "schema_version": int(raw.get("schema_version") or WORKFLOW_SCHEMA_VERSION),
        "id": workflow_id,
        "title": title,
        "subtitle": str(raw.get("subtitle") or "").strip(),
        "purpose_handle": str(raw.get("purpose_handle") or "general").strip(),
        "prompt": prompt,
        "system_image": str(raw.get("system_image") or "sparkles").strip(),
        "requires_foreground_review": bool(raw.get("requires_foreground_review", False)),
        "capabilities": _normalize_capabilities(
            raw.get("capabilities", ()),
            workflow_id=workflow_id,
        ),
        "flow_pattern": _normalize_flow_pattern(raw.get("flow_pattern"), workflow_id),
        "source_ports": _normalize_source_ports(raw.get("source_ports", ()), workflow_id),
        "artifact_types": _normalize_artifact_types(raw.get("artifact_types", ()), workflow_id),
        "approval_policy": _normalize_policy_map(raw.get("approval_policy")),
        "retention": _normalize_retention(raw.get("retention")),
    }
    guidance = str(raw.get("agent_guidance") or "").strip()
    if guidance:
        normalized["agent_guidance"] = guidance
    return normalized


def _load_workflow_config(path: Path) -> tuple[str, tuple[dict[str, Any], ...]]:
    if not path.is_file():
        return (
            DEFAULT_FIRST_WORKFLOW_TEMPLATE_ID,
            tuple(
                _normalize_workflow(workflow, i, strict_schema=True)
                for i, workflow in enumerate(_BUILTIN_WORKFLOWS)
            ),
        )
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise WorkflowConfigError(f"unparseable workflows config: {path} — {e}") from e
    if not isinstance(data, dict):
        raise WorkflowConfigError(f"workflows config must be a mapping: {path}")
    raw_workflows = data.get("workflows") or []
    if not isinstance(raw_workflows, list) or not raw_workflows:
        raise WorkflowConfigError(f"'workflows' must be a non-empty list: {path}")
    workflows = tuple(
        _normalize_workflow(item, i, strict_schema=True) for i, item in enumerate(raw_workflows)
    )
    first_id = str(data.get("first_workflow_id") or workflows[0]["id"]).strip()
    if not any(workflow["id"] == first_id for workflow in workflows):
        raise WorkflowConfigError(f"first_workflow_id {first_id!r} not found in workflows")
    return first_id, workflows


def _normalize_capabilities(raw: Any, *, workflow_id: str) -> list[str]:
    values = _normalize_string_list(raw, field_name="capabilities", workflow_id=workflow_id)
    if not values:
        raise WorkflowConfigError(f"workflow {workflow_id!r} declares no capabilities")
    out: list[str] = []
    for value in values:
        try:
            out.append(CapabilityKind(value).value)
        except ValueError as e:
            raise WorkflowConfigError(
                f"workflow {workflow_id!r} references unknown capability {value!r}",
            ) from e
    return sorted(set(out))


def _normalize_flow_pattern(raw: Any, workflow_id: str) -> str:
    value = str(raw or "").strip()
    if not value:
        raise WorkflowConfigError(f"workflow {workflow_id!r} missing flow_pattern")
    if value not in _ALLOWED_FLOW_PATTERNS:
        raise WorkflowConfigError(
            f"workflow {workflow_id!r} has unsupported flow_pattern {value!r}",
        )
    return value


def _normalize_source_ports(raw: Any, workflow_id: str) -> list[str]:
    values = _normalize_string_list(raw, field_name="source_ports", workflow_id=workflow_id)
    for value in values:
        if value not in _ALLOWED_SOURCE_PORTS:
            raise WorkflowConfigError(
                f"workflow {workflow_id!r} references unsupported source_port {value!r}",
            )
    return sorted(set(values))


def _normalize_artifact_types(raw: Any, workflow_id: str) -> list[str]:
    values = _normalize_string_list(raw, field_name="artifact_types", workflow_id=workflow_id)
    for value in values:
        if value not in _ALLOWED_ARTIFACT_TYPES:
            raise WorkflowConfigError(
                f"workflow {workflow_id!r} references unsupported artifact_type {value!r}",
            )
    return sorted(set(values))


def _normalize_string_list(raw: Any, *, field_name: str, workflow_id: str) -> list[str]:
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, list | tuple | set | frozenset):
        values = [str(value).strip() for value in raw]
    else:
        raise WorkflowConfigError(f"workflow {workflow_id!r} field {field_name} must be a list")
    values = [value for value in values if value]
    if not values:
        raise WorkflowConfigError(f"workflow {workflow_id!r} field {field_name} is empty")
    return values


def _normalize_policy_map(raw: Any) -> dict[str, str]:
    policy = dict(_DEFAULT_APPROVAL_POLICY)
    if raw is None:
        return policy
    if not isinstance(raw, dict):
        raise WorkflowConfigError("approval_policy must be a mapping")
    for key, value in raw.items():
        policy[str(key)] = str(value)
    for key in _DEFAULT_APPROVAL_POLICY:
        if not policy.get(key):
            raise WorkflowConfigError(f"approval_policy missing {key!r}")
    return policy


def _normalize_retention(raw: Any) -> dict[str, str]:
    retention = dict(_DEFAULT_RETENTION)
    if raw is None:
        return retention
    if not isinstance(raw, dict):
        raise WorkflowConfigError("retention must be a mapping")
    for key, value in raw.items():
        retention[str(key)] = str(value)
    return retention


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
