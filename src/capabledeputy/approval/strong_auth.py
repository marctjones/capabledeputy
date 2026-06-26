"""Shared strong-authentication requirements for approval requests."""

from __future__ import annotations

from typing import Any

from capabledeputy.approval.model import ApprovalAction, ApprovalRequest


def approval_requires_strong_auth(request: ApprovalRequest) -> bool:
    if request.action in {
        ApprovalAction.QUEUE_PURCHASE,
        ApprovalAction.EXECUTE_DESTRUCTIVE,
    }:
        return True
    labels = request.labels_in.to_dict()
    rendered = str(labels).lower()
    return any(
        token in rendered for token in ("financial", "health", "restricted", "prohibited")
    )


def approval_to_client_dict(
    request: ApprovalRequest,
    *,
    touch_id_policy_enabled: bool | None = None,
) -> dict[str, Any]:
    payload = request.to_dict()
    payload["requires_strong_auth"] = approval_requires_strong_auth(request)
    if touch_id_policy_enabled is not None:
        payload["touch_id_policy_enabled"] = touch_id_policy_enabled
    return payload