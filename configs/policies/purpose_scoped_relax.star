# Starter policy (Issue #47) — purpose-scoped autonomy.
#
# Relaxes the standard REQUIRE_APPROVAL prompt to ALLOW for a benign,
# explicitly opted-in purpose, cutting approval fatigue where the operator
# has already decided this workflow is low-stakes. Relax is bounded: it can
# only loosen WITHIN the envelope cell and can never override a structural
# DENY floor (enforced downstream by bounded-relax + monotone composition),
# and this script only fires when the base decision was REQUIRE_APPROVAL.
#
# This starter is deliberately narrow: by default it only relaxes local
# notifications for the `research` purpose. Do not relax email, chat, purchase,
# document edit, clipboard, or calendar mutation without a specific operator
# decision and tests for the workflow.
# See the header of sensitive_egress_confirm.star for the input shapes.

def inspect(action, session, proposed_outcome):
    if proposed_outcome["decision"] != "require_approval":
        return abstain()
    # Only relax for an opted-in low-stakes purpose, and only when the session
    # carries no high-tier data.
    if (
        "restricted" in session["tiers"]
        or "regulated" in session["tiers"]
        or "prohibited" in session["tiers"]
    ):
        return abstain()
    if session["purpose"] == "research" and action["kind"] in ["MACOS_NOTIFICATION"]:
        return relax(
            to="allow",
            rule="research-notification-autonomy",
            rationale="low-stakes research purpose may show local completion notifications",
        )
    return abstain()
