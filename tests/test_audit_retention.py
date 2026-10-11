from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from capabledeputy.audit.retention import (
    inventory_legacy_tool_outputs,
    sanitize_legacy_tool_outputs,
)
from capabledeputy.audit.verify import verify_audit_chain
from capabledeputy.cli.main import app

runner = CliRunner()


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


def test_sanitize_creates_separate_redacted_chain_and_manifest(tmp_path: Path) -> None:
    source = tmp_path / "audit.jsonl"
    source.write_text(
        "\n".join(
            json.dumps(record)
            for record in [
                {"audit_id": "created", "event_type": "session.created", "payload": {}},
                {
                    "audit_id": "legacy",
                    "event_type": "tool.returned",
                    "payload": {"output": "private connector content"},
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    sanitized = tmp_path / "audit.sanitized.jsonl"

    result = sanitize_legacy_tool_outputs(source, sanitized, operator="operator@example.test")

    assert source.read_text(encoding="utf-8").find("private connector content") >= 0
    assert sanitized.read_text(encoding="utf-8").find("private connector content") == -1
    assert result.sanitized_tool_returned_events == 1
    assert result.manifest.is_file()
    assert verify_audit_chain(sanitized).ok
    manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
    assert "private connector content" not in result.manifest.read_text(encoding="utf-8")
    assert manifest["source_modified"] is False
    assert manifest["cutover_performed"] is False
    assert manifest["operator"] == "operator@example.test"


def test_sanitize_refuses_malformed_source_without_writing_destination(tmp_path: Path) -> None:
    source = tmp_path / "audit.jsonl"
    source.write_text("not-json\n", encoding="utf-8")
    sanitized = tmp_path / "audit.sanitized.jsonl"

    try:
        sanitize_legacy_tool_outputs(source, sanitized, operator="operator@example.test")
    except ValueError as exc:
        assert "malformed" in str(exc)
    else:
        raise AssertionError("expected malformed audit source to be refused")
    assert not sanitized.exists()


def test_sanitize_cli_requires_explicit_operator_approval(tmp_path: Path) -> None:
    source = tmp_path / "audit.jsonl"
    source.write_text(
        json.dumps(
            {
                "audit_id": "legacy",
                "event_type": "tool.returned",
                "payload": {"output": "private connector content"},
            },
        )
        + "\n",
        encoding="utf-8",
    )
    sanitized = tmp_path / "audit.sanitized.jsonl"

    refused = runner.invoke(
        app,
        ["audit", "sanitize-legacy", str(source), str(sanitized), "--operator", "operator"],
    )
    assert refused.exit_code == 2
    assert not sanitized.exists()
    approved = runner.invoke(
        app,
        [
            "audit",
            "sanitize-legacy",
            str(source),
            str(sanitized),
            "--operator",
            "operator",
            "--approve",
            "--json",
        ],
    )
    assert approved.exit_code == 0
    assert "private connector content" not in approved.stdout
    assert sanitized.is_file()
