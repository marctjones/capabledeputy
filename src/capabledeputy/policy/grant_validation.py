"""Improvement roadmap #8 — grant pattern validator.

Today `/grant SEND_EMAIL spouse` succeeds quietly: the daemon
accepts the literal pattern "spouse" and stores a capability that
will never match anything real (the chokepoint's fnmatch against
`to` arg sees "spouse@example.com", not "spouse"). The operator
thinks they granted authority; the capability is dead. Fail-quietly
is the worst UX in a security tool.

This module runs per-kind shape checks against the pattern and
returns operator-readable warnings. The chat REPL's `/grant`
command surfaces them and asks for confirmation before submission.
We INTENTIONALLY don't block: operators may have legitimate
unusual conventions (e.g., custom MCP servers that interpret
patterns differently), and a hard reject would force them to bypass
the warning system entirely.

The validator is a pure function over (kind, pattern) — no side
effects, deterministic, easy to test.
"""

from __future__ import annotations

import re

from capabledeputy.policy.capabilities import CapabilityKind


def validate_grant_pattern(
    kind: CapabilityKind | str,
    pattern: str,
) -> list[str]:
    """Return zero or more operator-visible warnings about
    `pattern` for the given capability `kind`. Empty list means the
    pattern looks well-formed for the kind. Each warning is a
    full English sentence suitable for chat REPL display.

    Wildcard `*` is always accepted without warning — it's the
    operator's explicit "any" choice. Custom (namespaced) kinds
    skip validation: we don't know their target shape semantics.
    """
    # Wildcard always passes
    if pattern == "*":
        return []
    # Custom kinds (namespaced via `:`) skip validation — the
    # daemon's CustomKindRegistry defines per-kind semantics that
    # this module doesn't know about.
    if isinstance(kind, str) and ":" in kind:
        return []
    if not isinstance(kind, CapabilityKind):
        try:
            kind = CapabilityKind(kind)
        except ValueError:
            # Unrecognized kind — let the daemon reject downstream;
            # we don't want to second-guess the kind itself here.
            return []

    return _CHECK_FOR_KIND.get(kind, lambda _p: [])(pattern)


# --- per-kind validators ------------------------------------------------


_EMAIL_RE = re.compile(r"^[\w!#$%&'*+/=?^`{|}~.\-]+@[\w.\-]+$")


def _check_send_email(pattern: str) -> list[str]:
    """SEND_EMAIL expects an email-shape pattern (with optional `*`
    wildcards) — the policy engine matches against the action's
    `to` arg, which is a literal address. A pattern without `@`
    will never match."""
    # Allow patterns like `*@example.com`, `spouse@*`, `dad@x.com`.
    # The presence of `@` is a strong indicator the operator
    # understood what they were typing.
    if "@" not in pattern:
        return [
            f"pattern {pattern!r} has no '@' — SEND_EMAIL grants match "
            "against a recipient address (e.g. 'dad@example.com' or "
            "'*@example.com'). A pattern without '@' will never match "
            "any real send. Did you mean a relationship group rule "
            "instead?",
        ]
    return []


def _check_send_message(pattern: str) -> list[str]:
    """SEND_MESSAGE targets conversation ids, user ids, or wildcards."""
    if pattern.startswith("/") or "://" in pattern:
        return [
            f"pattern {pattern!r} looks like a path or URL — SEND_MESSAGE "
            "grants match chat/message destinations such as 'spaces/*', "
            "a user/conversation id, or '*'.",
        ]
    return []


def _check_queue_purchase(pattern: str) -> list[str]:
    """QUEUE_PURCHASE matches against vendor identifiers. Email-
    shape patterns are almost certainly a copy-paste mistake."""
    if "@" in pattern:
        return [
            f"pattern {pattern!r} looks like an email address — "
            "QUEUE_PURCHASE matches against a vendor identifier "
            "(e.g. 'amazon', 'walmart'). Did you mean SEND_EMAIL?",
        ]
    return []


def _check_fs_path(pattern: str) -> list[str]:
    """Filesystem-shaped kinds (READ_FS / CREATE_FS / MODIFY_FS /
    DELETE_FS / WRITE_FS) match against absolute paths. A
    relative-looking pattern without a leading `/` is suspicious."""
    if not pattern.startswith("/") and not pattern.startswith("~"):
        return [
            f"pattern {pattern!r} is not an absolute path — filesystem "
            "grants match against the action's target path, which is "
            "always absolute. Did you mean '/home/marc/" + pattern + "' "
            "or a wildcard like '/home/marc/Documents/*'?",
        ]
    if pattern.startswith("~"):
        return [
            f"pattern {pattern!r} starts with '~' — the policy engine "
            "matches against literal absolute paths and does NOT "
            "expand '~'. Use '/home/<your-user>/...' instead.",
        ]
    return []


def _check_web_fetch(pattern: str) -> list[str]:
    """WEB_FETCH matches against URLs the tool dispatches. A
    pattern without a scheme is unlikely to match real fetches."""
    if not (
        pattern.startswith("http://")
        or pattern.startswith("https://")
        or "*" in pattern  # `*` and `https://*` style allowed
    ):
        return [
            f"pattern {pattern!r} has no http(s):// scheme — WEB_FETCH "
            "grants match against the URL the agent fetches. Did you "
            f"mean 'https://{pattern}/*' or 'https://*.{pattern}/*'?",
        ]
    return []


def _check_calendar(pattern: str) -> list[str]:
    """Calendar kinds match against calendar identifiers — usually
    a calendar id or email-shaped address. A bare slug like
    'work' or 'personal' may or may not match depending on the
    operator's calendar tool conventions, so we only warn on the
    most obvious mistakes (paths)."""
    if pattern.startswith("/"):
        return [
            f"pattern {pattern!r} looks like a filesystem path — "
            "CALENDAR_* grants match against calendar IDs, not paths. "
            "Use the calendar id or a glob like 'calendar:personal*'.",
        ]
    return []


def _check_execute(pattern: str) -> list[str]:
    """EXECUTE_SANDBOX / EXECUTE_DEVBOX match against region spec
    ids declared in daemon.yaml's `sandbox.regions` block. A path-
    or URL-shaped pattern is almost certainly wrong."""
    if pattern.startswith("/") or "://" in pattern:
        return [
            f"pattern {pattern!r} looks like a path or URL — execute "
            "kinds match against region spec ids declared in "
            "daemon.yaml's sandbox.regions block (e.g. 'scratch', "
            "'py-dev'). Use the spec id or '*' for any region.",
        ]
    return []


def _check_external_read(pattern: str) -> list[str]:
    """External read kinds accept either a wildcard
    or a tool-specific filter expression. We can't fully validate
    the filter (it varies by tool), but we can flag obvious
    mismatches like absolute paths (which would match nothing)."""
    if pattern.startswith("/"):
        return [
            f"pattern {pattern!r} is a filesystem path — external-read "
            "grants match "
            "against the tool's query expression, not a local path. "
            "Use '*' or a query like 'from:boss@example.com'.",
        ]
    return []


def _check_browser_automation(pattern: str) -> list[str]:
    """Browser automation grants should normally be wildcard or URL-shaped."""
    if pattern.startswith("/"):
        return [
            f"pattern {pattern!r} looks like a filesystem path — "
            "BROWSER_AUTOMATION grants match browser targets, usually '*' "
            "or an http(s) URL pattern.",
        ]
    return []


def _check_macos_automation(pattern: str) -> list[str]:
    """macOS automation grants should normally be wildcard or bundle-id shaped."""
    if pattern.startswith("/") or "://" in pattern:
        return [
            f"pattern {pattern!r} looks like a path or URL — "
            "MACOS_AUTOMATION grants match local app/tool targets, usually '*' "
            "or a bundle id like 'com.apple.mail'.",
        ]
    return []


def _check_local_app(pattern: str) -> list[str]:
    """Bounded local-app grants match app/document targets, not web URLs."""
    if "://" in pattern:
        return [
            f"pattern {pattern!r} looks like a URL — local app automation "
            "grants match local app/document targets such as '*', a bundle id, "
            "or an app-specific document name.",
        ]
    return []


_CHECK_FOR_KIND: dict[CapabilityKind, callable] = {  # type: ignore[type-arg]
    CapabilityKind.SEND_EMAIL: _check_send_email,
    CapabilityKind.SEND_MESSAGE: _check_send_message,
    CapabilityKind.QUEUE_PURCHASE: _check_queue_purchase,
    CapabilityKind.READ_FS: _check_fs_path,
    CapabilityKind.WRITE_FS: _check_fs_path,
    CapabilityKind.CREATE_FS: _check_fs_path,
    CapabilityKind.MODIFY_FS: _check_fs_path,
    CapabilityKind.DELETE_FS: _check_fs_path,
    CapabilityKind.WEB_FETCH: _check_web_fetch,
    CapabilityKind.BROWSER_AUTOMATION: _check_browser_automation,
    CapabilityKind.BROWSER_READ: _check_web_fetch,
    CapabilityKind.BROWSER_NAVIGATE: _check_web_fetch,
    CapabilityKind.BROWSER_INTERACT: _check_browser_automation,
    CapabilityKind.BROWSER_SCRIPT: _check_browser_automation,
    CapabilityKind.BROWSER_FILE: _check_browser_automation,
    CapabilityKind.MACOS_AUTOMATION: _check_macos_automation,
    CapabilityKind.MACOS_APP_CONTROL: _check_macos_automation,
    CapabilityKind.MACOS_CLIPBOARD_READ: _check_macos_automation,
    CapabilityKind.MACOS_CLIPBOARD_WRITE: _check_macos_automation,
    CapabilityKind.MACOS_NOTIFICATION: _check_macos_automation,
    CapabilityKind.APPLE_MAIL_READ: _check_external_read,
    CapabilityKind.APPLE_MAIL_DRAFT: _check_send_email,
    CapabilityKind.KEYNOTE_READ: _check_local_app,
    CapabilityKind.KEYNOTE_PRESENT: _check_local_app,
    CapabilityKind.PAGES_READ: _check_local_app,
    CapabilityKind.PAGES_EDIT: _check_local_app,
    CapabilityKind.PAGES_EXPORT: _check_local_app,
    CapabilityKind.NUMBERS_READ: _check_local_app,
    CapabilityKind.NUMBERS_EDIT: _check_local_app,
    CapabilityKind.NUMBERS_EXPORT: _check_local_app,
    CapabilityKind.CALENDAR_READ: _check_calendar,
    CapabilityKind.CALENDAR_WRITE: _check_calendar,
    CapabilityKind.CREATE_CAL: _check_calendar,
    CapabilityKind.MODIFY_CAL: _check_calendar,
    CapabilityKind.DELETE_CAL: _check_calendar,
    CapabilityKind.EXECUTE_SANDBOX: _check_execute,
    CapabilityKind.EXECUTE_DEVBOX: _check_execute,
    CapabilityKind.GMAIL_READ: _check_external_read,
    CapabilityKind.GMAIL_DRAFT: _check_send_email,
    CapabilityKind.IMAP_READ: _check_external_read,
    CapabilityKind.DRIVE_READ: _check_external_read,
    CapabilityKind.CHAT_READ: _check_external_read,
    CapabilityKind.PEOPLE_READ: _check_external_read,
    CapabilityKind.GENERATE_IMAGE: _check_web_fetch,
    CapabilityKind.FETCH_IMAGE: _check_web_fetch,
}
