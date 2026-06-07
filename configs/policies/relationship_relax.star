# Starter policy (Issue #47) — relationship-aware relax.
#
# Softens the approval prompt to ALLOW when the recipient is in a trusted
# relationship group (e.g. "family"), cutting friction for routine
# communication with people the operator has vetted. Bounded: only relaxes
# a REQUIRE_APPROVAL base (the chokepoint refuses any relax that would
# cross a structural DENY/OVERRIDE floor), and only when no restricted
# data is in the session.
#
# Requires RelationshipGroups configured (configs/relationship_groups.yaml);
# the recipient's groups are resolved at the chokepoint and surfaced as
# action["relationship_groups"]. Edit the group name + action kinds to taste.

def inspect(action, session, proposed_outcome):
    if proposed_outcome["decision"] != "require_approval":
        return abstain()
    if "restricted" in session["tiers"]:
        return abstain()
    if "family" in action["relationship_groups"] and action["kind"] == "SEND_EMAIL":
        return relax(
            to="allow",
            rule="family-email-autonomy",
            rationale="recipient is in the vetted 'family' relationship group",
        )
    return abstain()
