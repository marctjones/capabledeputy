# Starter policy — low-integrity background inputs can suggest, not overwrite.
#
# This encodes the practical Biba-shaped rule used for cases like emailed bank
# statements: third-party documents can create review artifacts, but cannot
# overwrite trusted system-of-record profiles/records without an override.

def inspect(action, session, proposed_outcome):
    if proposed_outcome["decision"] == "deny":
        return abstain()
    if session.get("origin", {}).get("kind") != "onguard":
        return abstain()

    write_kinds = [
        "WRITE_FILE",
        "PAGES_EDIT",
        "NUMBERS_EDIT",
        "WORD_EDIT",
        "POWERPOINT_EDIT",
        "MEMORY_WRITE",
        "PROFILE_UPDATE",
        "SOURCE_BINDING_UPDATE",
    ]
    low_integrity = (
        "low" in session["provenance"]
        or "untrusted" in session["provenance"]
        or "external-untrusted" in session["provenance"]
    )
    if action["kind"] in write_kinds and low_integrity:
        return tighten(
            to="require_approval",
            rule="onguard-low-integrity-write-review",
            rationale="low-integrity onguard input may create suggestions but needs approval before overwriting trusted records",
        )

    return abstain()
