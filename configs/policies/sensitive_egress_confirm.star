# Starter policy (Issue #47) — defense-in-depth confirmation on egress of
# sensitive data.
#
# Adds a REQUIRE_APPROVAL prompt to any egress action that would otherwise
# auto-ALLOW while the session carries restricted/regulated data. This runs
# AFTER the structural floors (BLP/Biba/capability/conflict invariants),
# which always apply first — a script can only *tighten* here, never cross a
# DENY floor.
#
# Inputs a script sees (hermetic — no clock, no I/O, no host objects):
#   action          = {"kind": str, "target": str, "amount": int|None}
#   session         = {"purpose": str, "categories": [str], "tiers": [str],
#                      "provenance": [str], "risk_preference": str}
#   proposed_outcome= {"decision": str, "rule": str, "reason": str}
# Return relax(to=, rule=, rationale=) / tighten(...) / abstain().

def inspect(action, session, proposed_outcome):
    if proposed_outcome["decision"] != "allow":
        return abstain()
    egress_kinds = ["SEND_EMAIL", "QUEUE_PURCHASE"]
    sensitive = "restricted" in session["tiers"] or "regulated" in session["tiers"]
    if action["kind"] in egress_kinds and sensitive:
        return tighten(
            to="require_approval",
            rule="sensitive-egress-confirm",
            rationale="egress while session carries restricted/regulated data",
        )
    return abstain()
