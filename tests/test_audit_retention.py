from __future__ import annotations

import json
from pathlib import Path

from capabledeputy.audit.retention import inventory_legacy_tool_outputs


def test_inventory_classifies_without_returning_legacy_output(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    records = [
        {"audit_id": "created", "event_type": "session.created", "payload": {}},
        {
            "audit_id": "safe",
            "event_type": "tool.returned",
            "payload": {"output_redacted": True, "output_digest": "a" * 64},
        },
        {
            "audit_id": "legacy",
            "event_type": "tool.returned",
            "payload": {"output": "private connector content"},
        },
    ]
    audit.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")

    report = inventory_legacy_tool_outputs(audit)

    assert report.total_events == 3
    assert report.tool_returned_events == 2
    assert report.redacted_tool_returned_events == 1
    assert report.legacy_output_candidates == ("legacy",)
    rendered = report.to_dict()
    assert rendered["content_rendered"] is False
    assert rendered["legacy_output_candidate_sample"] == ["legacy"]
    assert "private connector content" not in repr(rendered)


def test_inventory_treats_malformed_lines_as_operator_review(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    audit.write_text("not-json\n", encoding="utf-8")

    report = inventory_legacy_tool_outputs(audit)

    assert report.malformed_lines == 1
    assert report.remediation_required is True
