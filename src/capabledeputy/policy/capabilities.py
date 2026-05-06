"""Capabilities held by sessions (DESIGN.md §7.3).

Capabilities are unforgeable tokens granting specific scoped actions.
The runtime — never the LLM — holds and dispatches them. Each
capability records its origin, expiry, and audit_id so every check
is traceable.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Self
from uuid import UUID, uuid4


class CapabilityKind(StrEnum):
    READ_FS = "READ_FS"
    WRITE_FS = "WRITE_FS"
    SEND_EMAIL = "SEND_EMAIL"
    WEB_FETCH = "WEB_FETCH"
    CALENDAR_READ = "CALENDAR_READ"
    CALENDAR_WRITE = "CALENDAR_WRITE"
    QUEUE_PURCHASE = "QUEUE_PURCHASE"


class CapabilityExpiry(StrEnum):
    ONE_SHOT = "one_shot"
    SESSION = "session"
    PERSISTENT = "persistent"


class CapabilityOrigin(StrEnum):
    SYSTEM_DEFAULT = "system_default"
    USER_APPROVED = "user_approved"
    PATTERN_RULE = "pattern_rule"


@dataclass(frozen=True)
class Capability:
    kind: CapabilityKind
    pattern: str
    expiry: CapabilityExpiry = CapabilityExpiry.SESSION
    origin: CapabilityOrigin = CapabilityOrigin.SYSTEM_DEFAULT
    audit_id: UUID = field(default_factory=uuid4)
    max_amount: int | None = None

    def matches(
        self,
        kind: CapabilityKind,
        target: str,
        amount: int | None = None,
    ) -> bool:
        if self.kind != kind:
            return False
        if not fnmatch.fnmatchcase(target, self.pattern):
            return False
        if self.max_amount is None:
            return True
        return amount is not None and amount <= self.max_amount

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "pattern": self.pattern,
            "expiry": self.expiry.value,
            "origin": self.origin.value,
            "audit_id": str(self.audit_id),
            "max_amount": self.max_amount,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        return cls(
            kind=CapabilityKind(d["kind"]),
            pattern=d["pattern"],
            expiry=CapabilityExpiry(d["expiry"]),
            origin=CapabilityOrigin(d["origin"]),
            audit_id=UUID(d["audit_id"]),
            max_amount=d.get("max_amount"),
        )
