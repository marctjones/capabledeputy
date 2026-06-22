# Starter policy — onguard clients may only run declared workflows.
#
# Onguard clients are ordinary daemon clients. They should not gain ambient
# authority just because they run headlessly. This inspector tightens any
# unapproved onguard-origin action and blocks client/workflow mismatches when
# a schedule metadata declaration is present.

def _origin(session):
    return session.get("origin", {})

def _metadata(session):
    return _origin(session).get("metadata", {})

def inspect(action, session, proposed_outcome):
    if proposed_outcome["decision"] == "deny":
        return abstain()

    origin = _origin(session)
    if origin.get("kind") != "onguard":
        return abstain()

    if not origin.get("approved_by"):
        return tighten(
            to="require_approval",
            rule="onguard-unapproved-origin",
            rationale="onguard action is missing explicit schedule/config approval metadata",
        )

    allowed = _metadata(session).get("allowed_workflows", [])
    workflow = _metadata(session).get("workflow")
    if workflow and allowed and workflow not in allowed:
        return tighten(
            to="deny",
            rule="onguard-workflow-not-declared",
            rationale="onguard client attempted a workflow outside its declared allowlist",
        )

    return abstain()
