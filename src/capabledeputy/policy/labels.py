"""Information-flow labels (DESIGN.md §7.1).

Labels classify information by sensitivity and provenance. The set
defined here is the v0.1 MVP scope. Future versions can extend the
enum or migrate to an open-set representation if needed.
"""

from __future__ import annotations

from enum import StrEnum


class Label(StrEnum):
    CONFIDENTIAL_HEALTH = "confidential.health"
    CONFIDENTIAL_FINANCIAL = "confidential.financial"
    CONFIDENTIAL_PERSONAL = "confidential.personal"
    UNTRUSTED_EXTERNAL = "untrusted.external"
    UNTRUSTED_USER_INPUT = "untrusted.user_input"
    TRUSTED_USER_DIRECT = "trusted.user_direct"
    EGRESS_EMAIL = "egress.email"
    EGRESS_PURCHASE = "egress.purchase"
