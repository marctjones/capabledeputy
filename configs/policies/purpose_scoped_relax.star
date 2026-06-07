# Starter policy (Issue #47) — purpose-scoped autonomy.
#
# Relaxes the standard REQUIRE_APPROVAL prompt to ALLOW for a benign,
# explicitly opted-in purpose, cutting approval fatigue where the operator
# has already decided this workflow is low-stakes. Relax is bounded: it can
# only loosen WITHIN the envelope cell and can never override a structural
# DENY floor (enforced downstream by bounded-relax + monotone composition),
# and this script only fires when the base decision was REQUIRE_APPROVAL.
#
# Edit the purpose name + action kinds to match your own purposes.yaml.
# See the header of sensitive_egress_confirm.star for the input shapes.

def inspect(action, session, proposed_outcome):
    if proposed_outcome["decision"] != "require_approval":
        return abstain()
    # Only relax for an opted-in low-stakes purpose, and only when the
    # session carries no restricted data (defense in depth — don't grant
    # autonomy over the most sensitive tier).
    if "restricted" in session["tiers"]:
        return abstain()
    if session["purpose"] == "daily_briefing" and action["kind"] in ["SEND_EMAIL"]:
        return relax(
            to="allow",
            rule="daily-briefing-autonomy",
            rationale="opted-in low-stakes purpose with no restricted data in session",
        )
    return abstain()
