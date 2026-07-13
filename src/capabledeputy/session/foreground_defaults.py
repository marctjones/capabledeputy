"""Standing capability grants for foreground chat surfaces (GUI, REPL).

CLI chat pre-grants read caps on auto-created sessions; the Swift GUI and
other foreground clients should get the same treatment when a purpose does
not contribute defaults (unknown purpose) or the session is born empty.
"""

from __future__ import annotations

import os
from uuid import uuid4

from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityExpiry,
    CapabilityKind,
    CapabilityOrigin,
)

FOREGROUND_CHAT_OWNERS: frozenset[str] = frozenset(
    {
        "CapDepMac",
        "capdep-chat",
        "capdep-tui",
    },
)

FOREGROUND_PURPOSE_HANDLES: frozenset[str] = frozenset(
    {
        "general",
        "inbox",
        "calendar",
        "writing",
        "research",
    },
)


def _home_prefixes(home: str) -> tuple[str, ...]:
    return (
        f"{home}/Documents/*",
        f"{home}/Projects/*",
        f"{home}/Downloads/*",
        f"{home}/Desktop/*",
        f"{home}/notes/**",
        f"{home}/.capdep/work/*",
    )


def foreground_chat_default_capabilities(
    *,
    home: str | None = None,
) -> tuple[Capability, ...]:
    """Safe read-oriented caps for interactive chat without manual /grant."""
    resolved_home = home or os.path.expanduser("~")
    caps: list[Capability] = []
    for pattern in _home_prefixes(resolved_home):
        caps.append(_make_cap(CapabilityKind.READ_FS, pattern))
    caps.extend(
        (
            _make_cap(CapabilityKind.CREATE_FS, f"{resolved_home}/.capdep/work/*"),
            _make_cap(CapabilityKind.CREATE_FS, "/tmp/*"),
            _make_cap(CapabilityKind.GMAIL_READ, "*"),
            _make_cap(CapabilityKind.IMAP_READ, "*"),
            _make_cap(CapabilityKind.DRIVE_READ, "*"),
            _make_cap(CapabilityKind.CHAT_READ, "*"),
            _make_cap(CapabilityKind.PEOPLE_READ, "*"),
            _make_cap(CapabilityKind.CALENDAR_READ, "*"),
            _make_cap(CapabilityKind.WEB_FETCH, "*"),
            _make_cap(CapabilityKind.GENERATE_IMAGE, "*"),
            _make_cap(CapabilityKind.FETCH_IMAGE, "*"),
            _make_cap(CapabilityKind.APPLE_MAIL_READ, "*"),
            _make_cap(CapabilityKind.OUTLOOK_READ, "*"),
            _make_cap(CapabilityKind.WORD_READ, "*"),
            _make_cap(CapabilityKind.POWERPOINT_READ, "*"),
            _make_cap(CapabilityKind.EXECUTE_SANDBOX, "scratch"),
        ),
    )
    return tuple(caps)


def _missing_foreground_image_caps(capability_set: frozenset[Capability]) -> bool:
    kinds = {cap.kind for cap in capability_set}
    return CapabilityKind.GENERATE_IMAGE not in kinds or CapabilityKind.FETCH_IMAGE not in kinds


def _missing_foreground_web_search_cap(capability_set: frozenset[Capability]) -> bool:
    return not any(
        cap.kind is CapabilityKind.WEB_FETCH and cap.pattern == "*" for cap in capability_set
    )


def should_apply_foreground_defaults(
    *,
    owner: str | None,
    purpose_handle: str,
    capability_count: int,
    capability_set: frozenset[Capability] | None = None,
) -> bool:
    owner_key = (owner or "").strip()
    foreground_owner = owner_key in FOREGROUND_CHAT_OWNERS
    foreground_purpose = purpose_handle in FOREGROUND_PURPOSE_HANDLES
    if not foreground_owner and not foreground_purpose:
        return False
    if capability_count == 0:
        return True
    return capability_set is not None and (
        _missing_foreground_image_caps(capability_set)
        or _missing_foreground_web_search_cap(capability_set)
    )


def supplement_foreground_capabilities(
    capability_set: frozenset[Capability],
    *,
    home: str | None = None,
) -> tuple[Capability, ...]:
    """Grant only foreground caps missing from an existing purpose-born set."""
    existing_kinds = {cap.kind for cap in capability_set}
    additions: list[Capability] = []
    for cap in foreground_chat_default_capabilities(home=home):
        if cap.kind is CapabilityKind.WEB_FETCH:
            if _missing_foreground_web_search_cap(capability_set):
                additions.append(cap)
            continue
        if cap.kind not in existing_kinds:
            additions.append(cap)
    return tuple(additions)


def _make_cap(kind: CapabilityKind, pattern: str) -> Capability:
    return Capability(
        kind=kind,
        pattern=pattern,
        origin=CapabilityOrigin.USER_APPROVED,
        audit_id=uuid4(),
        expiry=CapabilityExpiry.SESSION,
        allows_destructive=False,
        revoked_by=frozenset(),
        expires_at=None,
        rate_limit=None,
    )
