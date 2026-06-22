"""Packaged onguard client handlers.

These handlers are intentionally deterministic examples. They produce labeled
artifacts, queue follow-up commands, or emit recommendations through daemon RPCs;
they do not call external tools or mutate trusted state directly.
"""

from __future__ import annotations

from typing import Any, Protocol

from capabledeputy.onguard.runtime import OnguardHandler, OnguardTask


class OnguardDaemonLike(Protocol):
    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any: ...


DEFAULT_ONGUARD_CLIENT_IDS: tuple[str, ...] = (
    "onguard.digest.daily",
    "onguard.inbox.triage",
    "onguard.meeting.prep",
    "onguard.watch_folder",
    "onguard.knowledge.update",
    "onguard.task.followup",
    "onguard.research.monitor",
    "onguard.desktop.monitor",
    "onguard.finance.guard",
    "onguard.approval.deterministic",
)


def packaged_handlers(daemon: OnguardDaemonLike) -> dict[str, OnguardHandler]:
    """Return built-in handlers for the default onguard client set."""
    return {
        "schedule": lambda task: _default_schedule_handler(daemon, task),
        "build_daily_digest": lambda task: _artifact_handler(
            daemon,
            task,
            artifact_type="daily_digest",
            title="Daily personal newspaper digest",
            summary="Prepared a digest draft from approved, labeled inputs.",
        ),
        "triage_inbox": lambda task: _artifact_handler(
            daemon,
            task,
            artifact_type="inbox_triage",
            title="Inbox triage recommendations",
            summary="Prepared read-only inbox triage buckets for human review.",
        ),
        "prepare_meeting": lambda task: _artifact_handler(
            daemon,
            task,
            artifact_type="meeting_prep",
            title="Meeting prep brief",
            summary="Prepared a meeting brief from approved calendar context.",
        ),
        "process_watch_folder": lambda task: _artifact_handler(
            daemon,
            task,
            artifact_type="watch_folder_review",
            title="Watch-folder processing recommendations",
            summary="Prepared safe file-processing recommendations without moving files.",
        ),
        "update_knowledge": lambda task: _artifact_handler(
            daemon,
            task,
            artifact_type="knowledge_update_candidate",
            title="Knowledge update candidate",
            summary="Prepared a low-integrity knowledge update candidate for approval.",
        ),
        "follow_up_tasks": lambda task: _artifact_handler(
            daemon,
            task,
            artifact_type="task_followup",
            title="Task follow-up recommendations",
            summary="Prepared follow-up reminders for human review.",
        ),
        "monitor_research": lambda task: _artifact_handler(
            daemon,
            task,
            artifact_type="research_monitor",
            title="Research monitor results",
            summary="Prepared research-monitor findings from labeled external inputs.",
        ),
        "monitor_desktop": lambda task: _artifact_handler(
            daemon,
            task,
            artifact_type="desktop_monitor",
            title="Desktop automation observations",
            summary="Prepared macOS automation observations without acting on applications.",
        ),
        "guard_finance_document": lambda task: _finance_guard_handler(daemon, task),
        "deterministic_approval_sweep": lambda task: _approval_sweep_handler(
            daemon,
            task,
        ),
    }


async def _default_schedule_handler(
    daemon: OnguardDaemonLike,
    task: OnguardTask,
) -> dict[str, Any]:
    command = str(task.record.get("command") or task.record.get("schedule_id") or "schedule")
    return await _artifact_handler(
        daemon,
        task,
        artifact_type="scheduled_summary",
        title=f"Scheduled onguard run: {command}",
        summary="Recorded a scheduled onguard run; configure a command-specific handler for work.",
    )


async def _artifact_handler(
    daemon: OnguardDaemonLike,
    task: OnguardTask,
    *,
    artifact_type: str,
    title: str,
    summary: str,
) -> dict[str, Any]:
    content = {
        "title": title,
        "summary": summary,
        "client_id": task.client_id,
        "command": task.command,
        "payload": task.payload,
        "requires_human_review": True,
    }
    artifact = await daemon.call(
        "artifact.create",
        {
            "client_id": task.client_id,
            "command_id": task.record.get("command_id"),
            "schedule_id": task.record.get("schedule_id"),
            "artifact_type": artifact_type,
            "content": content,
            "labels": _labels(task, "onguard.output", "review.required"),
            "provenance": {
                "origin": "onguard",
                "client_id": task.client_id,
                "command": task.command,
            },
        },
    )
    artifact_id = artifact.get("artifact", {}).get("artifact_id")
    return {
        "ok": True,
        "artifact_ref": artifact_id,
        "summary": summary,
        "requires_human_review": True,
    }


async def _finance_guard_handler(
    daemon: OnguardDaemonLike,
    task: OnguardTask,
) -> dict[str, Any]:
    source = str(task.payload.get("source") or "unknown")
    trusted = source in {"bank_api", "trusted_bank_integration"}
    if trusted:
        return await _artifact_handler(
            daemon,
            task,
            artifact_type="finance_reconciliation_candidate",
            title="Trusted finance reconciliation candidate",
            summary="Prepared trusted integration data for reconciliation.",
        )
    artifact = await daemon.call(
        "artifact.create",
        {
            "client_id": task.client_id,
            "command_id": task.record.get("command_id"),
            "schedule_id": task.record.get("schedule_id"),
            "artifact_type": "finance_document_quarantine",
            "content": {
                "title": "Finance document requires explicit approval",
                "summary": (
                    "Untrusted finance input was quarantined and must not overwrite "
                    "trusted records without human override."
                ),
                "payload": task.payload,
                "requires_human_override": True,
            },
            "labels": _labels(task, "finance", "external-untrusted", "review.required"),
            "provenance": {
                "origin": "onguard",
                "client_id": task.client_id,
                "source": source,
            },
        },
    )
    return {
        "ok": True,
        "blocked": True,
        "artifact_ref": artifact.get("artifact", {}).get("artifact_id"),
        "reason": "untrusted finance document requires human override",
    }


async def _approval_sweep_handler(
    daemon: OnguardDaemonLike,
    task: OnguardTask,
) -> dict[str, Any]:
    """Deterministic approval helper for safe denials.

    This only denies requests matching explicit configured rules. It does not
    approve sensitive or irreversible actions by default.
    """
    deny_rules = list(task.payload.get("deny_rules") or [])
    approvals = await daemon.call("approval.list", {"status": "pending", "limit": 100})
    denied: list[int] = []
    for item in approvals.get("approvals", []):
        action = str(item.get("action") or item.get("tool") or "")
        target = str(item.get("target") or "")
        for rule in deny_rules:
            if _rule_matches(rule, action=action, target=target):
                approval_id = int(item["id"])
                await daemon.call(
                    "approval.deny",
                    {
                        "id": approval_id,
                        "decided_by": task.client_id,
                        "reason": f"deterministic deny rule matched: {rule}",
                    },
                )
                denied.append(approval_id)
                break
    return {"ok": True, "denied": denied, "approved": []}


def _rule_matches(rule: Any, *, action: str, target: str) -> bool:
    if not isinstance(rule, dict):
        return False
    action_prefix = str(rule.get("action_prefix") or "")
    target_contains = str(rule.get("target_contains") or "")
    if action_prefix and not action.startswith(action_prefix):
        return False
    if target_contains and target_contains not in target:
        return False
    return bool(action_prefix or target_contains)


def _labels(task: OnguardTask, *extra: str) -> list[str]:
    labels = [str(label) for label in task.labels]
    for label in extra:
        if label not in labels:
            labels.append(label)
    return labels
