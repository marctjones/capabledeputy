"""Pattern approval rules: auto-approve future requests matching a pattern.

The user can register a rule that says, e.g., "any SEND_EMAIL to
wife@example.com with any payload, auto-approve for the next 24 hours."
Future requests that match the pattern are auto-approved on submit
but still surfaced as APPROVAL_REQUESTED + APPROVAL_APPROVED audit
events so nothing is silent.

Strict pattern validation prevents footguns: patterns containing `*`
in critical positions (recipient, vendor) are rejected — auto-
approving "send to anywhere" is the kind of mistake a hostile prompt
injection could exploit later.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from capabledeputy.approval.model import ApprovalAction, ApprovalRequest


class PatternValidationError(ValueError):
    pass


class PatternMatch(StrEnum):
    EXACT = "exact"
    GLOB = "glob"


_FORBIDDEN_GLOBS: frozenset[str] = frozenset({"*", "**", "*@*", "?", "[*]"})


def _validate_target_pattern(pattern: str) -> None:
    if not pattern or pattern.strip() == "":
        raise PatternValidationError("target pattern is empty")
    stripped = pattern.strip()
    if stripped in _FORBIDDEN_GLOBS:
        raise PatternValidationError(
            f"target pattern '{pattern}' is too permissive; "
            "specify a concrete recipient or a domain-scoped glob "
            "like '*@example.com', not a free-floating '*'",
        )
    if stripped.startswith("*") and not re.match(r"^\*@[\w.-]+$", stripped):
        raise PatternValidationError(
            f"target pattern '{pattern}' starts with '*' without a "
            "domain anchor; only '*@DOMAIN' style globs are allowed",
        )


@dataclass(frozen=True)
class ApprovalPatternRule:
    id: UUID
    action: ApprovalAction
    target_pattern: str
    payload_pattern: str | None
    created_at: datetime
    expires_at: datetime
    created_by: str
    revoked: bool = False
    auto_approval_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        action: ApprovalAction,
        target_pattern: str,
        ttl: timedelta,
        created_by: str = "user",
        payload_pattern: str | None = None,
    ) -> ApprovalPatternRule:
        _validate_target_pattern(target_pattern)
        if ttl <= timedelta(0):
            raise PatternValidationError("ttl must be positive")
        if ttl > timedelta(days=30):
            raise PatternValidationError("ttl cannot exceed 30 days")
        now = datetime.now(UTC)
        return cls(
            id=uuid4(),
            action=action,
            target_pattern=target_pattern,
            payload_pattern=payload_pattern,
            created_at=now,
            expires_at=now + ttl,
            created_by=created_by,
        )

    def is_expired(self, now: datetime | None = None) -> bool:
        return (now or datetime.now(UTC)) >= self.expires_at

    def matches(self, request: ApprovalRequest) -> bool:
        if self.revoked or self.is_expired():
            return False
        if request.action != self.action:
            return False
        if not fnmatch.fnmatchcase(request.target, self.target_pattern):
            return False
        if self.payload_pattern is None:
            return True
        return fnmatch.fnmatchcase(request.payload, self.payload_pattern)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "action": self.action.value,
            "target_pattern": self.target_pattern,
            "payload_pattern": self.payload_pattern,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "created_by": self.created_by,
            "revoked": self.revoked,
            "auto_approval_count": self.auto_approval_count,
        }


class ApprovalPatternRegistry:
    def __init__(self) -> None:
        self._rules: dict[UUID, ApprovalPatternRule] = {}

    def add(self, rule: ApprovalPatternRule) -> None:
        self._rules[rule.id] = rule

    def revoke(self, rule_id: UUID) -> ApprovalPatternRule | None:
        rule = self._rules.get(rule_id)
        if rule is None:
            return None
        from dataclasses import replace as _replace

        revoked = _replace(rule, revoked=True)
        self._rules[rule_id] = revoked
        return revoked

    def list(self) -> list[ApprovalPatternRule]:
        return list(self._rules.values())

    def find_match(self, request: ApprovalRequest) -> ApprovalPatternRule | None:
        for rule in self._rules.values():
            if rule.matches(request):
                return rule
        return None

    def increment_use(self, rule_id: UUID) -> None:
        rule = self._rules.get(rule_id)
        if rule is None:
            return
        from dataclasses import replace as _replace

        self._rules[rule_id] = _replace(
            rule,
            auto_approval_count=rule.auto_approval_count + 1,
        )
