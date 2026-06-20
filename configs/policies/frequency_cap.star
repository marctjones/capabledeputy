# Starter policy (Issue #47/#48) — frequency cap.
#
# Tightens to REQUIRE_APPROVAL once an action kind has been used many times
# in a session, catching runaway loops / over-eager automation. Uses the
# read-only history summary threaded into `session` by #48:
#
#   session["history"]["counts_by_kind"] = {"SEND_EMAIL": 4, ...}  # cumulative
#   session["history"]["used_kinds"]     = ["SEND_EMAIL", ...]
#   session["history"]["total_uses"]     = int
#
# Counts are session-cumulative (clock-free — scripts have no clock). Defaults
# cover social egress, drafts, local app automation, document edits, and
# calendar mutations.

def _threshold_for(kind):
    if kind in ["SEND_EMAIL", "SEND_MESSAGE", "QUEUE_PURCHASE"]:
        return 5
    if kind in ["GMAIL_DRAFT", "APPLE_MAIL_DRAFT"]:
        return 10
    if kind in ["MACOS_APP_CONTROL", "MACOS_CLIPBOARD_READ", "MACOS_CLIPBOARD_WRITE"]:
        return 12
    if kind in ["PAGES_EDIT", "NUMBERS_EDIT", "KEYNOTE_PRESENT"]:
        return 20
    if kind in ["CREATE_CAL", "MODIFY_CAL", "DELETE_CAL"]:
        return 10
    return None

def inspect(action, session, proposed_outcome):
    if proposed_outcome["decision"] != "allow":
        return abstain()
    counts = session["history"]["counts_by_kind"]
    threshold = _threshold_for(action["kind"])
    if threshold != None and counts.get(action["kind"], 0) >= threshold:
        return tighten(
            to="require_approval",
            rule="session-frequency-cap",
            rationale="session action count reached configured confirmation threshold",
        )
    return abstain()
