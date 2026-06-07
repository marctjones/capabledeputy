"""Policy decision outcomes.

Enum of the four possible results from the policy engine.
"""

from __future__ import annotations

from enum import StrEnum


class Decision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"
    # 003 US6 T079 — distinct outcome for crossing a hard floor.
    # Ordinary approval cannot resolve; the operator must run the
    # `capdep override request` (and `attest` for dual-control) path
    # to mint a capability with origin=override_granted (FR-038).
    OVERRIDE_REQUIRED = "override_required"
