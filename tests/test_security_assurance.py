from __future__ import annotations

from capabledeputy.security_assurance import (
    flow_composition_items,
    model_sidecar_boundaries,
    reference_monitor_totality_items,
    security_assurance_plan,
    source_coverage_fixtures,
    substrate_contracts,
)


def test_v50_security_assurance_plan_covers_all_tracks() -> None:
    plan = security_assurance_plan()

    assert plan["schema"] == "capdep.security_assurance_plan.v1"
    tracks = {item["track"] for item in plan["items"]}
    assert tracks >= {
        "reference_monitor_totality",
        "flow_pattern_composition",
        "label_source_coverage",
        "real_substrate_contracts",
        "warn_advisory_tier",
        "model_sidecar_boundaries",
    }


def test_reference_monitor_totality_items_are_test_backed() -> None:
    items = reference_monitor_totality_items()

    assert any(item.item_id == "tool-client-chokepoint" for item in items)
    assert all(item.evidence for item in items)
    assert all("Reference Monitor" in item.security_property or item.track for item in items)


def test_flow_composition_items_capture_known_failure_modes() -> None:
    item_ids = {item.item_id for item in flow_composition_items()}

    assert "declassification-scoping" in item_ids
    assert "restricted-reference-or-sealed" in item_ids
    assert "containment-not-declassification" in item_ids
    assert "parallel-disclosure-budget" in item_ids


def test_source_coverage_fixtures_include_explicit_limits() -> None:
    fixtures = source_coverage_fixtures()
    labels = {label for fixture in fixtures for label in fixture.expected_labels}

    assert {"health:regulated", "financial:regulated", "external-untrusted"} <= labels
    assert any(fixture.coverage_limit for fixture in fixtures)


def test_substrate_contracts_are_non_destructive_by_default() -> None:
    contracts = substrate_contracts()

    assert {contract.substrate_id for contract in contracts} >= {
        "google-workspace",
        "imap",
        "office-automation",
        "sandbox",
        "local-models-and-media",
    }
    assert all(contract.live_mode == "fake_or_dry_run_by_default" for contract in contracts)
    assert all(
        "policy_decided_before_mutation" in contract.required_assertions for contract in contracts
    )


def test_model_sidecar_boundaries_forbid_policy_authority() -> None:
    boundaries = model_sidecar_boundaries()

    assert {boundary.sidecar_id for boundary in boundaries} >= {
        "guard.sidecar",
        "reranker.default",
        "image-generation",
    }
    for boundary in boundaries:
        assert "authorize" in boundary.forbidden_effects
        assert "declassify_without_certified_path" in boundary.forbidden_effects
        assert "weaken_hard_floor" in boundary.forbidden_effects
