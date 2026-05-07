"""RPC handlers for approval pattern rules."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

from capabledeputy.app import App
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
        try:
            rule = ApprovalPatternRule.create(
                action=ApprovalAction(params["action"]),
                target_pattern=str(params["target_pattern"]),
                ttl=timedelta(hours=float(params.get("ttl_hours", 24))),
                created_by=str(params.get("created_by", "user")),
                payload_pattern=params.get("payload_pattern"),
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

    return {
        "approval_pattern.list": pattern_list,
        "approval_pattern.create": pattern_create,
        "approval_pattern.revoke": pattern_revoke,
    }
