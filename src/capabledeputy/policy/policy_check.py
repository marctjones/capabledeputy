"""#385 — one validation entry point over the compiled policy.

`docs/policy-authoring-design.md` §8/§9: `capdep policy check` loads the unified
document, validates every cross-reference in ONE pass, runs the #307 requirement
gate, and reports ALL problems at once (not the first). This module is the
testable core; `cli/policy.py` wraps it.

Cross-references checked:
  - a rule's `crosses_floor` names a real structural floor,
  - a rule / requirement references a category that the catalog declares,
  - a posture's `inspector_set` names a known inspector,
  - the built-in + operator requirements hold against the selected posture.
"""

from __future__ import annotations

from dataclasses import dataclass

from capabledeputy.policy.authoring import CompiledPolicy
from capabledeputy.policy.decision_inspector_loader import _BUILTIN_DEFAULT_FACTORIES
from capabledeputy.policy.overrides import structural_floor_for_rule
from capabledeputy.policy.requirements import Requirement, verify_requirements

_KNOWN_INSPECTORS: frozenset[str] = frozenset(_BUILTIN_DEFAULT_FACTORIES)


@dataclass(frozen=True)
class PolicyProblem:
    """One issue found in a policy. `severity` is 'error' (refuses start) or
    'warning' (surfaced, non-blocking)."""

    where: str
    message: str
    severity: str = "error"


def _rule_categories(predicate) -> list[str]:
    cats = list(predicate.axis_a_categories)
    if predicate.axis_a_category is not None:
        cats.append(predicate.axis_a_category)
    return cats


def check_policy(
    compiled: CompiledPolicy,
    *,
    custom_requirements: tuple[Requirement, ...] = (),
) -> list[PolicyProblem]:
    """Validate a compiled policy and return EVERY problem (empty ⇒ clean).

    Collects all problems rather than failing on the first, so an operator sees
    the whole picture in one run."""
    problems: list[PolicyProblem] = []
    known_categories = set(compiled.categories)

    # 1. rule crosses_floor must be a real structural floor.
    for rule in compiled.rules.rules:
        if rule.crosses_floor is not None and structural_floor_for_rule(rule.crosses_floor) is None:
            problems.append(
                PolicyProblem(
                    f"rule {rule.rule_id!r}",
                    f"crosses_floor {rule.crosses_floor!r} is not a structural floor",
                ),
            )
        # 2. rule category references must exist when a catalog is declared.
        if known_categories:
            for cat in _rule_categories(rule.predicate):
                if cat not in known_categories:
                    problems.append(
                        PolicyProblem(
                            f"rule {rule.rule_id!r}",
                            f"references category {cat!r} not declared in labels:",
                            severity="warning",
                        ),
                    )

    # 3. posture inspector_set names must be known.
    if compiled.posture is not None:
        for name in compiled.posture.inspector_set:
            if name not in _KNOWN_INSPECTORS:
                problems.append(
                    PolicyProblem(
                        f"posture {compiled.posture.id!r}",
                        f"inspector_set names unknown inspector {name!r} "
                        f"(known: {sorted(_KNOWN_INSPECTORS)})",
                    ),
                )

    # 4. requirement category references must exist when a catalog is declared.
    if known_categories:
        for req in custom_requirements:
            if req.category and req.category not in known_categories:
                problems.append(
                    PolicyProblem(
                        f"requirement {req.id!r}",
                        f"references category {req.category!r} not declared in labels:",
                        severity="warning",
                    ),
                )

    # 5. the #307 requirement gate against the selected posture.
    if compiled.posture is not None:
        for result in verify_requirements(
            posture=compiled.posture,
            custom=custom_requirements,
        ):
            if not result.satisfied:
                problems.append(
                    PolicyProblem(
                        f"requirement {result.requirement.id}",
                        result.detail,
                    ),
                )

    return problems


def has_errors(problems: list[PolicyProblem]) -> bool:
    """True iff any problem is error-severity (would refuse daemon start / CI)."""
    return any(p.severity == "error" for p in problems)
