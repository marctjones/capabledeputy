# Starter policy (Issue #47) — defense-in-depth confirmation on egress of
# sensitive data.
#
# Adds a REQUIRE_APPROVAL prompt to any egress or externally materialized
# draft/calendar/message action that would otherwise auto-ALLOW while the
# session carries restricted/regulated/prohibited data. This runs
# AFTER the structural floors (BLP/Biba/capability/conflict invariants),
# which always apply first — a script can only *tighten* here, never cross a
# DENY floor.
#
# Inputs a script sees (hermetic — no clock, no I/O, no host objects):
#   action          = {"kind": str, "target": str, "amount": int|None,
#                      "relationship_groups": [str]}
#   session         = {"purpose": str, "categories": [str], "tiers": [str],
#                      "provenance": [str], "risk_preference": str,
#                      "history": {"counts_by_kind": {kind: int},
#                                  "used_kinds": [str], "total_uses": int}}
#   proposed_outcome= {"decision": str, "rule": str, "reason": str}
# Return relax(to=, rule=, rationale=) / tighten(...) / abstain().

def inspect(action, session, proposed_outcome):
    if proposed_outcome["decision"] != "allow":
        return abstain()
    egress_kinds = [
        "SEND_EMAIL",
        "SEND_MESSAGE",
        "QUEUE_PURCHASE",
        "GMAIL_DRAFT",
        "APPLE_MAIL_DRAFT",
        "CREATE_CAL",
        "MODIFY_CAL",
    ]
    sensitive = (
        "restricted" in session["tiers"]
        or "regulated" in session["tiers"]
        or "prohibited" in session["tiers"]
    )
    if action["kind"] in egress_kinds and sensitive:
        return tighten(
            to="require_approval",
            rule="sensitive-egress-confirm",
            rationale="egress or externally materialized draft while session carries high-tier data",
        )
    return abstain()
