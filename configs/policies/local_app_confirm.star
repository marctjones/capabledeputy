# Production policy — first-use confirmation for local desktop automation.
#
# Local AppleScript/macOS tools are bounded and argv-safe, but they still act
# on ambient desktop state: the frontmost Pages/Numbers/Keynote document, the
# system clipboard, visible Mail drafts, notifications, and focused apps. This
# script keeps those workflows practical by prompting once per session/action
# kind instead of requiring approval for every read or edit.

def _count_for(action, session):
    return session["history"]["counts_by_kind"].get(action["kind"], 0)

def inspect(action, session, proposed_outcome):
    if proposed_outcome["decision"] != "allow":
        return abstain()

    first_use_kinds = [
        "MACOS_APP_CONTROL",
        "MACOS_CLIPBOARD_READ",
        "MACOS_CLIPBOARD_WRITE",
        "MACOS_NOTIFICATION",
        "APPLE_MAIL_DRAFT",
        "GMAIL_DRAFT",
        "KEYNOTE_PRESENT",
        "PAGES_EDIT",
        "PAGES_EXPORT",
        "NUMBERS_EDIT",
        "NUMBERS_EXPORT",
        "CREATE_CAL",
        "MODIFY_CAL",
        "DELETE_CAL",
    ]
    if action["kind"] in first_use_kinds and _count_for(action, session) == 0:
        return tighten(
            to="require_approval",
            rule="local-active-first-use-confirm",
            rationale="first use of local app, clipboard, draft, or calendar mutation this session",
        )

    return abstain()
