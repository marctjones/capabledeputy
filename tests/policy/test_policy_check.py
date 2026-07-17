"""#385 — check_policy: one-pass cross-reference validation over a compiled
policy, reporting ALL problems at once + running the #307 requirement gate."""

from __future__ import annotations

from capabledeputy.policy.authoring import compile_document
from capabledeputy.policy.policy_check import PolicyProblem, check_policy, has_errors
from capabledeputy.policy.requirements import Requirement, RequirementKind


def _errors(problems: list[PolicyProblem]) -> list[str]:
    return [p.message for p in problems if p.severity == "error"]


def _warnings(problems: list[PolicyProblem]) -> list[str]:
    return [p.message for p in problems if p.severity == "warning"]


def test_clean_policy_has_no_problems() -> None:
    doc = {
        "posture": {"id": "strict", "dial": "cautious"},
        "labels": [{"category": "financial", "tier": "restricted"}],
        "rules": [{"id": "r", "when": "financial + send_email", "then": "deny"}],
    }
    problems = check_policy(compile_document(doc))
    assert problems == []
    assert not has_errors(problems)


def test_bad_crosses_floor_is_an_error() -> None:
    doc = {
        "rules": [
            {
                "id": "r",
                "when": "financial + send_email",
                "then": "allow",
                "human_ratified_by": "owner",
                "crosses_floor": "not-a-real-floor",
            },
        ],
    }
    problems = check_policy(compile_document(doc))
    assert has_errors(problems)
    assert any("not a structural floor" in m for m in _errors(problems))


def test_valid_crosses_floor_is_accepted() -> None:
    doc = {
        "rules": [
            {
                "id": "r",
                "when": "health + send_email",
                "then": "allow",
                "human_ratified_by": "owner",
                "crosses_floor": "health-meets-egress",
            },
        ],
    }
    assert not has_errors(check_policy(compile_document(doc)))


def test_undeclared_category_is_a_warning_when_catalog_present() -> None:
    doc = {
        "labels": [{"category": "financial", "tier": "restricted"}],
        "rules": [{"id": "r", "when": "typo_category + send_email", "then": "deny"}],
    }
    problems = check_policy(compile_document(doc))
    assert not has_errors(problems)  # a warning, not an error
    assert any("not declared in labels" in m for m in _warnings(problems))


def test_no_category_warning_without_a_catalog() -> None:
    # No labels: section -> we can't know the category set, so no false positives.
    doc = {"rules": [{"id": "r", "when": "anything + send_email", "then": "deny"}]}
    assert check_policy(compile_document(doc)) == []


def test_unknown_inspector_in_posture_is_an_error() -> None:
    doc = {"posture": {"id": "p", "inspectors": ["made_up_inspector"]}}
    problems = check_policy(compile_document(doc))
    assert has_errors(problems)
    assert any("unknown inspector" in m for m in _errors(problems))


def test_known_inspector_in_posture_is_ok() -> None:
    doc = {"posture": {"id": "p", "inspectors": ["self_egress_relaxer"]}}
    assert not has_errors(check_policy(compile_document(doc)))


def test_requirement_gate_runs_against_the_posture() -> None:
    """A projection_only=false posture fails the built-in exposure... no — the
    exposure floor still holds; but the opt-in planner-blind requirement fails."""
    doc = {"posture": {"id": "p", "projection_only": False}}
    optin = Requirement(
        id="op.planner-blind",
        kind=RequirementKind.PLANNER_BLIND_TO_UNTRUSTED_SOURCE,
        description="planner blind",
    )
    problems = check_policy(compile_document(doc), custom_requirements=(optin,))
    assert has_errors(problems)
    assert any("op.planner-blind" in p.where for p in problems)


def test_collects_all_problems_at_once() -> None:
    """check_policy reports every problem, not just the first."""
    doc = {
        "posture": {"id": "p", "inspectors": ["bogus1", "bogus2"]},
        "rules": [
            {
                "id": "r",
                "when": "health",
                "then": "allow",
                "human_ratified_by": "owner",
                "crosses_floor": "bogus-floor",
            },
        ],
    }
    problems = check_policy(compile_document(doc))
    # two unknown inspectors + one bad crosses_floor = 3 errors, all surfaced.
    assert len(_errors(problems)) >= 3


def test_requirement_category_undeclared_is_a_warning() -> None:
    doc = {
        "posture": {"id": "p"},
        "labels": [{"category": "financial", "tier": "restricted"}],
    }
    req = Requirement(
        id="op.x",
        kind=RequirementKind.NEVER_SILENT_EGRESS,
        category="nonexistent",
        description="x",
    )
    problems = check_policy(compile_document(doc), custom_requirements=(req,))
    assert any(p.severity == "warning" and "not declared in labels" in p.message for p in problems)
