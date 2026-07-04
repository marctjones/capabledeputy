# Production policy — first-use confirmation for local desktop automation.
#
# Local AppleScript/macOS tools are bounded and argv-safe, but they still act
# on ambient desktop state: the frontmost Pages/Numbers/Keynote document, the
# system clipboard, visible Mail drafts, frontmost app documents, and focused
# apps. This script keeps those workflows practical by prompting once per
# session/action kind for active/ambient operations. Self/trusted drafts and
# self-calendar mutations are allowed to stay low-friction when the session is
# in the matching purpose and is not carrying high-tier data.

def _count_for(action, session):
    return session["history"]["counts_by_kind"].get(action["kind"], 0)

def _has_high_tier(session):
    return (
        "restricted" in session["tiers"]
        or "regulated" in session["tiers"]
        or "prohibited" in session["tiers"]
    )

def _has_group(action, group_ids):
    for group_id in group_ids:
        if group_id in action["relationship_groups"]:
            return True
    return False

def _low_friction_draft(action, session):
    if action["kind"] not in ["GMAIL_DRAFT", "APPLE_MAIL_DRAFT", "OUTLOOK_DRAFT"]:
        return False
    if _has_high_tier(session):
        return False
    if session["purpose"] not in ["inbox", "general", "writing"]:
        return False
    return _has_group(action, ["self", "trusted-draft", "family", "work-team"])

def _low_friction_calendar(action, session):
    if action["kind"] not in ["CREATE_CAL", "MODIFY_CAL"]:
        return False
    if _has_high_tier(session):
        return False
    if session["purpose"] != "calendar":
        return False
    return _has_group(action, ["self"])

def inspect(action, session, proposed_outcome):
    if proposed_outcome["decision"] != "allow":
        return abstain()

    if _low_friction_draft(action, session) or _low_friction_calendar(action, session):
        return abstain()

    first_use_kinds = [
        "MACOS_APP_CONTROL",
        "MACOS_CLIPBOARD_READ",
        "MACOS_CLIPBOARD_WRITE",
        "APPLE_MAIL_DRAFT",
        "OUTLOOK_DRAFT",
        "GMAIL_DRAFT",
        "KEYNOTE_PRESENT",
        "POWERPOINT_PRESENT",
        "PAGES_EDIT",
        "PAGES_EXPORT",
        "WORD_EDIT",
        "WORD_EXPORT",
        "NUMBERS_EDIT",
        "NUMBERS_EXPORT",
        "POWERPOINT_EDIT",
        "POWERPOINT_EXPORT",
        "CREATE_CAL",
        "MODIFY_CAL",
        "DELETE_CAL",
    ]
    if action["kind"] in first_use_kinds and _count_for(action, session) == 0:
        return tighten(
            to="require_approval",
            rule="local-active-first-use-confirm",
            rationale="first active use of local app, clipboard, external draft, or calendar mutation this session",
        )

    return abstain()
