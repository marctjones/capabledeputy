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
# Counts are session-cumulative (clock-free — scripts have no clock). Edit
# the kind + threshold to taste.

def inspect(action, session, proposed_outcome):
    if proposed_outcome["decision"] != "allow":
        return abstain()
    counts = session["history"]["counts_by_kind"]
    if action["kind"] == "SEND_EMAIL" and counts.get("SEND_EMAIL", 0) >= 5:
        return tighten(
            to="require_approval",
            rule="send-frequency-cap",
            rationale="5+ sends already this session — confirm before more",
        )
    return abstain()
