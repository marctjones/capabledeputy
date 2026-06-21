# Starter policy (Issue #47) — reversible-write autonomy.
#
# Relaxes REQUIRE_APPROVAL to ALLOW for local write-like actions only when the
# deterministic policy layer has already proved the current action is
# reversible by the system. This is intentionally narrow: it does not relax
# irreversible egress, purchases, destructive deletes, or high-tier sessions.

_WRITE_KINDS = [
    "CREATE_FS",
    "MODIFY_FS",
    "GMAIL_DRAFT",
    "APPLE_MAIL_DRAFT",
    "PAGES_EDIT",
    "NUMBERS_EDIT",
    "KEYNOTE_PRESENT",
    "CREATE_CAL",
    "MODIFY_CAL",
    "MEMORY_WRITE",
]

def inspect(action, session, proposed_outcome):
    if proposed_outcome["decision"] != "require_approval":
        return abstain()
    if action["kind"] not in _WRITE_KINDS:
        return abstain()
    if (
        "restricted" in session["tiers"]
        or "regulated" in session["tiers"]
        or "prohibited" in session["tiers"]
    ):
        return abstain()
    reversibility = session["reversibility"]
    if reversibility["degree"] == "reversible" and reversibility["agent"] == "system":
        return relax(
            to="allow",
            rule="reversible-system-write-auto",
            rationale="current write is deterministically reversible by the system",
        )
    return abstain()
