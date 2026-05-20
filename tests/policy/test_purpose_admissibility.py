"""T050 — Purpose-scoped category admissibility (FR-009 / SC-004).

A Purpose declares which data categories may be read in sessions
pursuing it. The marquee scenario from US3: `health` is excluded
from `employee-evaluation` because reading health data while
evaluating an employee would be unlawful in many jurisdictions and
inappropriate everywhere else. Spawning an employee-evaluation
session yields zero capabilities with read scope over health, and
no future grant or delegation can introduce one.

These tests exercise the pure-function admissibility surface; the
SessionGraph integration is covered in test_purpose_delegation_refusal
and test_no_purpose_failclosed.
"""

from __future__ import annotations

from pathlib import Path

from capabledeputy.policy.purposes import (
    Purpose,
    PurposeError,
    Purposes,
    categories_of_capability,
    load,
)


def _employee_eval_purpose() -> Purpose:
    return Purpose(
        purpose_id="employee-evaluation",
        label="Quarterly employee performance review",
        admissible_categories=frozenset({"work_performance", "proprietary_work"}),
        inadmissible_categories=frozenset({"health", "personal"}),
    )


def _drafting_purpose() -> Purpose:
    """A purpose with an empty explicit set — by design admits
    nothing. Useful for the unset/fail-closed semantic."""
    return Purpose(
        purpose_id="drafting",
        label="Drafting work in progress",
    )


def test_admissible_category_passes() -> None:
    p = _employee_eval_purpose()
    assert p.admits("work_performance")
    assert p.admits("proprietary_work")


def test_inadmissible_category_refused() -> None:
    p = _employee_eval_purpose()
    assert not p.admits("health")
    assert not p.admits("personal")


def test_unknown_category_refused() -> None:
    """A category not in the whitelist and not in the blacklist is
    still refused — the whitelist is exhaustive (FR-009 structural)."""
    p = _employee_eval_purpose()
    assert not p.admits("financial")


def test_empty_purpose_admits_nothing() -> None:
    """A purpose that declares neither whitelist nor blacklist
    admits nothing (conservative default)."""
    p = _drafting_purpose()
    assert not p.admits("work_performance")
    assert not p.admits("anything")


def test_blacklist_wins_over_whitelist() -> None:
    """If a category is in BOTH lists, the blacklist wins
    (most-restrictive, FR-024)."""
    p = Purpose(
        purpose_id="weird",
        admissible_categories=frozenset({"health"}),
        inadmissible_categories=frozenset({"health"}),
    )
    assert not p.admits("health")


def test_registry_admits_unknown_purpose_returns_false() -> None:
    """Lookup of an unregistered handle ⇒ admit nothing.
    This is the FR-046 fail-closed default for `unset`."""
    registry = Purposes(purposes={"employee-evaluation": _employee_eval_purpose()})
    assert not registry.admits("unset", "work_performance")
    assert not registry.admits("nonexistent", "work_performance")


def test_registry_admits_known_purpose() -> None:
    registry = Purposes(purposes={"employee-evaluation": _employee_eval_purpose()})
    assert registry.admits("employee-evaluation", "work_performance")
    assert not registry.admits("employee-evaluation", "health")


def test_categories_of_capability_returns_inadmissible_subset() -> None:
    """The helper that SessionGraph.new uses to filter candidate
    capabilities. Returns the categories that would be refused."""
    registry = Purposes(purposes={"employee-evaluation": _employee_eval_purpose()})
    inadmissible = categories_of_capability(
        cap_categories=frozenset({"work_performance", "health", "proprietary_work"}),
        purposes=registry,
        purpose_handle="employee-evaluation",
    )
    assert inadmissible == frozenset({"health"})


def test_categories_of_capability_unset_purpose_refuses_all() -> None:
    """The `unset` handle is not in the registry — every category is
    inadmissible. This is the FR-046 spawn-time check."""
    registry = Purposes(purposes={"employee-evaluation": _employee_eval_purpose()})
    inadmissible = categories_of_capability(
        cap_categories=frozenset({"work_performance", "health"}),
        purposes=registry,
        purpose_handle="unset",
    )
    assert inadmissible == frozenset({"work_performance", "health"})


# --- YAML loader fail-closed -----------------------------------------


def test_load_missing_file_fails_closed() -> None:
    try:
        load(Path("/nonexistent/purposes.yaml"))
    except PurposeError:
        return
    raise AssertionError("expected PurposeError")


def test_load_empty_yaml_yields_empty_registry(tmp_path: Path) -> None:
    f = tmp_path / "purposes.yaml"
    f.write_text("purposes: []", encoding="utf-8")
    registry = load(f)
    assert registry.purposes == {}
    assert not registry.admits("anything", "health")


def test_load_duplicate_purpose_id_fails_closed(tmp_path: Path) -> None:
    f = tmp_path / "purposes.yaml"
    f.write_text(
        "purposes:\n"
        "  - purpose_id: x\n"
        "    admissible_categories: [a]\n"
        "  - purpose_id: x\n"
        "    admissible_categories: [b]\n",
        encoding="utf-8",
    )
    try:
        load(f)
    except PurposeError:
        return
    raise AssertionError("expected PurposeError for duplicate")


def test_load_well_formed(tmp_path: Path) -> None:
    f = tmp_path / "purposes.yaml"
    f.write_text(
        "purposes:\n"
        "  - purpose_id: employee-evaluation\n"
        "    label: Quarterly review\n"
        "    admissible_categories: [work_performance, proprietary_work]\n"
        "    inadmissible_categories: [health, personal]\n",
        encoding="utf-8",
    )
    registry = load(f)
    p = registry.get("employee-evaluation")
    assert p is not None
    assert p.label == "Quarterly review"
    assert p.admits("work_performance")
    assert not p.admits("health")
