"""RPC handlers for approval pattern rules."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from capabledeputy.app import App
from capabledeputy.approval.library import (
    PatternLibraryError,
    apply_library,
    load_library_file,
)
from capabledeputy.approval.model import ApprovalAction
from capabledeputy.approval.pattern import (
    ApprovalPatternRule,
    PatternValidationError,
)
from capabledeputy.daemon.handlers import Handler


def make_pattern_handlers(app: App) -> dict[str, Handler]:
    async def pattern_list(params: dict[str, Any]) -> dict[str, Any]:
        return {"patterns": [r.to_dict() for r in app.approval_queue.patterns.list()]}

    async def pattern_create(params: dict[str, Any]) -> dict[str, Any]:
        # Issue #8 — optional labels_required + audit_tag.
        from capabledeputy.policy.labels import Label as _Label

        labels_in_raw = params.get("labels_required") or []
        try:
            labels_required = frozenset(_Label(s) for s in labels_in_raw)
        except ValueError as e:
            return {"error": f"invalid label in labels_required: {e}"}
        try:
            rule = ApprovalPatternRule.create(
                action=ApprovalAction(params["action"]),
                target_pattern=str(params["target_pattern"]),
                ttl=timedelta(hours=float(params.get("ttl_hours", 24))),
                created_by=str(params.get("created_by", "user")),
                payload_pattern=params.get("payload_pattern"),
                labels_required=labels_required,
                audit_tag=str(params.get("audit_tag", "")),
            )
        except PatternValidationError as e:
            return {"error": str(e)}
        app.approval_queue.patterns.add(rule)
        return rule.to_dict()

    async def pattern_revoke(params: dict[str, Any]) -> dict[str, Any]:
        revoked = app.approval_queue.patterns.revoke(UUID(params["id"]))
        if revoked is None:
            return {"error": "pattern not found"}
        return revoked.to_dict()

    async def pattern_import(params: dict[str, Any]) -> dict[str, Any]:
        path = Path(str(params["path"]))
        if not path.is_file():
            return {"error": f"library file not found: {path}"}
        try:
            entries = load_library_file(path)
            rules = apply_library(entries, app.approval_queue.patterns)
        except PatternLibraryError as e:
            return {"error": str(e)}
        return {"patterns": [r.to_dict() for r in rules]}

    return {
        "approval_pattern.list": pattern_list,
        "approval_pattern.create": pattern_create,
        "approval_pattern.revoke": pattern_revoke,
        "approval_pattern.import": pattern_import,
    }
