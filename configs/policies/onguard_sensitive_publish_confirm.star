# Starter policy — require approval for sensitive background publication.
#
# Background work may prepare drafts, reports, artifacts, or suggestions. It
# must not publish or externally materialize sensitive/untrusted outputs without
# explicit approval.

def _has_any(values, needles):
    for needle in needles:
        if needle in values:
            return True
    return False

def inspect(action, session, proposed_outcome):
    if proposed_outcome["decision"] != "allow":
        return abstain()
    if session.get("origin", {}).get("kind") != "onguard":
        return abstain()

    publish_kinds = [
        "SEND_EMAIL",
        "SEND_MESSAGE",
        "GMAIL_DRAFT",
        "APPLE_MAIL_DRAFT",
        "OUTLOOK_DRAFT",
        "CREATE_CAL",
        "MODIFY_CAL",
        "WRITE_FILE",
        "PAGES_EDIT",
        "PAGES_EXPORT",
        "NUMBERS_EDIT",
        "NUMBERS_EXPORT",
        "WORD_EDIT",
        "WORD_EXPORT",
        "POWERPOINT_EDIT",
        "POWERPOINT_EXPORT",
        "POWERPOINT_PRESENT",
    ]
    risky_tiers = ["restricted", "regulated", "prohibited"]
    risky_provenance = ["untrusted", "external", "low", "external-untrusted"]

    if action["kind"] in publish_kinds and (
        _has_any(session["tiers"], risky_tiers)
        or _has_any(session["provenance"], risky_provenance)
    ):
        return tighten(
            to="require_approval",
            rule="onguard-sensitive-publish-confirm",
            rationale="background publication/write carries sensitive or low-integrity data",
        )

    return abstain()
