"""Tests for `policy/purchase_reversibility.py` -- the per-call
reversibility resolver that lets configs/rules.yaml's
`purchases-under-threshold-auto` rule actually reach ALLOW for the
real `purchase.queue` tool (previously structurally unreachable: see
tests/policy/test_rules_yaml_real_registry.py and its companion fix).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.policy.purchase_reversibility import (
    PurchaseReversibilityError,
    PurchaseReversibilityPolicy,
    load_purchase_reversibility_policy,
    parse_purchase_reversibility_policy,
)
from capabledeputy.policy.reversibility import ReversalAgent, ReversibilityDegree


def test_empty_policy_never_matches() -> None:
    policy = PurchaseReversibilityPolicy()
    assert policy.resolve_for_vendor("amazon") is None


def test_load_absent_file_returns_empty_policy(tmp_path: Path) -> None:
    policy = load_purchase_reversibility_policy(tmp_path / "does-not-exist.yaml")
    assert policy == PurchaseReversibilityPolicy()


def test_load_shipped_default_config_is_empty() -> None:
    """The repo ships configs/purchase_reversibility.yaml with an
    empty list -- opt-in, no vendor gets special treatment until the
    operator declares one."""
    repo_root = Path(__file__).resolve().parents[2]
    policy = load_purchase_reversibility_policy(
        repo_root / "configs" / "purchase_reversibility.yaml",
    )
    assert policy == PurchaseReversibilityPolicy()


def test_case_insensitive_glob_match() -> None:
    policy = parse_purchase_reversibility_policy(
        {
            "purchase_reversibility": [
                {
                    "match": {"vendor_glob": "amazon*"},
                    "reversibility": {"degree": "reversible", "agent": "system"},
                },
            ],
        },
    )
    label = policy.resolve_for_vendor("Amazon-Fresh")
    assert label is not None
    assert label.degree is ReversibilityDegree.REVERSIBLE
    assert label.agent is ReversalAgent.SYSTEM


def test_no_match_returns_none() -> None:
    policy = parse_purchase_reversibility_policy(
        {
            "purchase_reversibility": [
                {
                    "match": {"vendor_glob": "amazon*"},
                    "reversibility": {"degree": "reversible", "agent": "system"},
                },
            ],
        },
    )
    assert policy.resolve_for_vendor("ticketmaster") is None


def test_multiple_vendor_globs_on_one_rule() -> None:
    policy = parse_purchase_reversibility_policy(
        {
            "purchase_reversibility": [
                {
                    "match": {"vendor_glob": ["amazon", "amazon-fresh"]},
                    "reversibility": {"degree": "reversible", "agent": "system"},
                },
            ],
        },
    )
    assert policy.resolve_for_vendor("amazon-fresh") is not None
    assert policy.resolve_for_vendor("amazon") is not None
    assert policy.resolve_for_vendor("amazon-prime-video") is None


def test_overlapping_rules_compose_most_restrictive() -> None:
    """Two entries both matching the same vendor: the stricter one
    wins (FR-037) -- narrowing an existing entry can never be
    silently loosened by a second, broader one."""
    policy = parse_purchase_reversibility_policy(
        {
            "purchase_reversibility": [
                {
                    "match": {"vendor_glob": "amazon*"},
                    "reversibility": {"degree": "reversible", "agent": "system"},
                },
                {
                    "match": {"vendor_glob": "amazon-fresh"},
                    "reversibility": {"degree": "reversible-with-friction", "agent": "human"},
                },
            ],
        },
    )
    label = policy.resolve_for_vendor("amazon-fresh")
    assert label is not None
    assert label.degree is ReversibilityDegree.REVERSIBLE_WITH_FRICTION
    assert label.agent is ReversalAgent.HUMAN


def test_missing_vendor_glob_is_fail_closed() -> None:
    with pytest.raises(PurchaseReversibilityError):
        parse_purchase_reversibility_policy(
            {
                "purchase_reversibility": [
                    {"match": {}, "reversibility": {"degree": "reversible", "agent": "system"}},
                ],
            },
        )


def test_missing_reversibility_block_is_fail_closed() -> None:
    with pytest.raises(PurchaseReversibilityError):
        parse_purchase_reversibility_policy(
            {
                "purchase_reversibility": [
                    {"match": {"vendor_glob": "amazon"}},
                ],
            },
        )


def test_invalid_reversibility_degree_is_fail_closed() -> None:
    with pytest.raises(PurchaseReversibilityError):
        parse_purchase_reversibility_policy(
            {
                "purchase_reversibility": [
                    {
                        "match": {"vendor_glob": "amazon"},
                        "reversibility": {"degree": "not-a-real-degree", "agent": "system"},
                    },
                ],
            },
        )


def test_empty_vendor_glob_list_is_fail_closed() -> None:
    with pytest.raises(PurchaseReversibilityError):
        parse_purchase_reversibility_policy(
            {
                "purchase_reversibility": [
                    {
                        "match": {"vendor_glob": []},
                        "reversibility": {"degree": "reversible", "agent": "system"},
                    },
                ],
            },
        )


def test_not_a_list_is_fail_closed() -> None:
    with pytest.raises(PurchaseReversibilityError):
        parse_purchase_reversibility_policy({"purchase_reversibility": "amazon"})


def test_load_unparseable_yaml_is_fail_closed(tmp_path: Path) -> None:
    bad = tmp_path / "purchase_reversibility.yaml"
    bad.write_text("purchase_reversibility: [unclosed", encoding="utf-8")
    with pytest.raises(PurchaseReversibilityError):
        load_purchase_reversibility_policy(bad)


def test_load_valid_file_round_trips(tmp_path: Path) -> None:
    p = tmp_path / "purchase_reversibility.yaml"
    p.write_text(
        "purchase_reversibility:\n"
        "  - match:\n"
        "      vendor_glob: amazon*\n"
        "    reversibility:\n"
        "      degree: reversible\n"
        "      agent: system\n",
        encoding="utf-8",
    )
    policy = load_purchase_reversibility_policy(p)
    assert policy.resolve_for_vendor("amazon") is not None
