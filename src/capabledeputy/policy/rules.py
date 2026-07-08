"""Policy decision outcomes.

Enum of the four possible results from the policy engine.
"""

from __future__ import annotations

from enum import StrEnum


class Decision(StrEnum):
    ALLOW = "allow"
    # v0.50 — non-blocking advisory. WARN proceeds like ALLOW after the
    # ordinary capability/policy checks have allowed the action, but it is
    # surfaced and audited so the operator sees the caution without approval
    # fatigue. It must never weaken DENY / OVERRIDE_REQUIRED /
    # REQUIRE_APPROVAL floors.
    WARN = "warn"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"
    # 003 US6 T079 — distinct outcome for crossing a hard floor.
    # Ordinary approval cannot resolve; the operator must run the
    # `capdep override request` (and `attest` for dual-control) path
    # to mint a capability with origin=override_granted (FR-038).
    OVERRIDE_REQUIRED = "override_required"
