from __future__ import annotations

from pathlib import Path

import yaml

from capabledeputy.daily_driver import Gate
from capabledeputy.daily_driver_validation import validate_daily_driver_workflows
from capabledeputy.policy.capabilities import CapabilityKind

_PRESET = Path(__file__).parent.parent / "configs" / "personal-assistant"


def test_daily_driver_validation_passes_every_user_facing_workflow() -> None:
    report = validate_daily_driver_workflows(preset_dir=_PRESET)

    assert report["schema"] == "capdep.daily_driver_workflow_validation.v1"
    assert report["ready"] is True
    assert report["blocked"] == []
    assert report["workflow_count"] >= 8

    by_id = {item["workflow_id"]: item for item in report["results"]}
    assert {
        "morning-briefing",
        "inbox-triage",
        "calendar-planning",
        "meeting-prep",
        "research-memo",
        "web-research",
        "summarize-selection",
        "revise-document",
    } <= set(by_id)

    assert by_id["morning-briefing"]["launch_gate"] == Gate.NO_APPROVAL.value
    assert by_id["morning-briefing"]["mutation_gate"] == Gate.NO_APPROVAL.value
    assert by_id["inbox-triage"]["egress_gate"] == Gate.REQUIRE_APPROVAL.value
    assert by_id["calendar-planning"]["review"] == "foreground_review_required"
    assert by_id["revise-document"]["review"] == "foreground_review_required"


def test_daily_driver_validation_catches_missing_purpose_capability(tmp_path: Path) -> None:
    preset = tmp_path / "preset"
    preset.mkdir()
    for name in ("purposes.yaml", "source_bindings.yaml"):
        (preset / name).write_text((_PRESET / name).read_text(encoding="utf-8"), encoding="utf-8")

    raw = yaml.safe_load((preset / "purposes.yaml").read_text(encoding="utf-8"))
    for purpose in raw["purposes"]:
        if purpose["purpose_id"] == "research":
            purpose["default_capabilities"] = [
                cap
                for cap in purpose["default_capabilities"]
                if cap["kind"] != CapabilityKind.BROWSER_READ.value
            ]
    (preset / "purposes.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    report = validate_daily_driver_workflows(preset_dir=preset)
    research = next(item for item in report["results"] if item["workflow_id"] == "research-memo")

    assert report["ready"] is False
    assert research["status"] == "blocked"
    assert CapabilityKind.BROWSER_READ.value in research["missing_capabilities"]


def test_daily_driver_validation_catches_unbound_source_port(tmp_path: Path) -> None:
    preset = tmp_path / "preset"
    preset.mkdir()
    for name in ("purposes.yaml", "source_bindings.yaml"):
        (preset / name).write_text((_PRESET / name).read_text(encoding="utf-8"), encoding="utf-8")

    raw = yaml.safe_load((preset / "source_bindings.yaml").read_text(encoding="utf-8"))
    raw["bindings"] = [
        binding
        for binding in raw["bindings"]
        if binding["name"] not in {"browser-active-page", "browser-https"}
    ]
    (preset / "source_bindings.yaml").write_text(
        yaml.safe_dump(raw, sort_keys=False),
        encoding="utf-8",
    )

    report = validate_daily_driver_workflows(preset_dir=preset)
    research = next(item for item in report["results"] if item["workflow_id"] == "research-memo")

    assert report["ready"] is False
    assert research["status"] == "blocked"
    assert "browser.current-page" in research["unbound_source_ports"]
