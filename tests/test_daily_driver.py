from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from capabledeputy.cli.setup_cli import app as setup_app
from capabledeputy.cli.setup_domains import setup_daily_driver
from capabledeputy.daily_driver import (
    POLICY_MATRIX,
    Gate,
    approval_preview,
    audit_minimized_payload,
    evaluate_tool_readiness,
    policy_contract_json,
    readiness_summary,
)

runner = CliRunner()


def _write_daemon_config(path: Path) -> None:
    path.write_text(
        """
upstream_servers:
  - name: bundled-fs
    tool_overrides:
      fs.read: {capability_kind: READ_FS}
      fs.create: {capability_kind: CREATE_FS}
      fs.write: {capability_kind: WRITE_FS}
      fs.delete: {capability_kind: DELETE_FS}
  - name: bundled-fetch
    inherent_labels: [untrusted.external]
    tool_overrides:
      fetch.get: {capability_kind: WEB_FETCH, target_arg: url}
  - name: bundled-search
    inherent_labels: [untrusted.external]
    tool_overrides:
      search.web: {capability_kind: WEB_FETCH, target_arg: query}
  - name: google-gmail
    disabled_kinds: [SEND_EMAIL]
    tool_overrides:
      gmail.search: {capability_kind: GMAIL_READ}
      gmail.create_draft: {capability_kind: GMAIL_DRAFT, target_arg: to}
  - name: google-calendar
    tool_overrides:
      calendar.read: {capability_kind: CALENDAR_READ}
      calendar.create: {capability_kind: CREATE_CAL, target_template: "gcal://{calendar_id}/{event_id}"}
      calendar.update: {capability_kind: MODIFY_CAL, target_template: "gcal://{calendar_id}/{event_id}"}
      calendar.delete: {capability_kind: DELETE_CAL, target_template: "gcal://{calendar_id}/{event_id}"}
""",
        encoding="utf-8",
    )


def test_daily_driver_policy_matrix_has_expected_gate_levels() -> None:
    by_workflow = {entry.workflow: entry for entry in POLICY_MATRIX}

    assert by_workflow["allowed-root reads and summaries"].gate == Gate.NO_APPROVAL
    assert by_workflow["external or state-changing actions"].gate == Gate.REQUIRE_APPROVAL
    assert by_workflow["sensitive declassification or generic automation"].gate == (
        Gate.OVERRIDE_REQUIRED
    )
    assert by_workflow["structural non-goals"].gate == Gate.DENY


def test_daily_driver_policy_contract_is_machine_readable() -> None:
    contract = json.loads(policy_contract_json())

    assert contract["schema"] == "capdep.daily_driver_policy.v1"
    assert any(entry["gate"] == "require_approval" for entry in contract["policy_matrix"])
    assert any(tool["tool_id"] == "browser" for tool in contract["tool_catalog"])
    assert any(
        rule["data_class"] == "secrets and credentials" for rule in contract["retention_rules"]
    )


def test_daily_driver_readiness_reports_available_degraded_and_disabled(tmp_path: Path) -> None:
    config = tmp_path / "daemon.yaml"
    _write_daemon_config(config)

    results = evaluate_tool_readiness(config)
    by_id = {result.entry.tool_id: result for result in results}
    summary = readiness_summary(results)

    assert by_id["local-files"].status == "available"
    assert by_id["web-search-fetch"].status == "available"
    assert by_id["gmail"].status == "available"
    assert by_id["calendar"].status == "available"
    assert by_id["direct-send"].status == "disabled_by_policy"
    assert by_id["browser"].status == "optional_missing"
    assert summary["ready"] is True
    assert summary["counts"]["available"] >= 4


def test_daily_driver_readiness_degrades_missing_required_tool(tmp_path: Path) -> None:
    config = tmp_path / "daemon.yaml"
    config.write_text("upstream_servers: []\n", encoding="utf-8")

    results = evaluate_tool_readiness(config)
    summary = readiness_summary(results)

    assert "local-files" in summary["blocking"]
    assert "web-search-fetch" in summary["blocking"]
    assert summary["ready"] is False


def test_approval_preview_and_audit_payload_redact_secrets() -> None:
    preview = approval_preview(
        action="SEND_EMAIL",
        target="client@example.com",
        tool="gmail.create_draft",
        capability="GMAIL_DRAFT",
        labels=("confidential.personal",),
        payload="subject\nbody access_token=abc123 sk-abcdefghijklmnopqrstuvwxyz",
    )
    audit = audit_minimized_payload(
        action="SEND_EMAIL",
        target="client@example.com",
        labels=("confidential.personal",),
        payload="refresh_token=abc123",
    )

    assert preview["state_changing"] is True
    assert preview["destination"] == "client@example.com"
    assert preview["payload_redacted"] is True
    assert "abc123" not in preview["payload_preview"]
    assert audit["payload_redacted"] is True
    assert "abc123" not in audit["payload_preview"]


def test_setup_daily_driver_dry_run_does_not_write(tmp_path: Path) -> None:
    config = tmp_path / "daemon.yaml"
    _write_daemon_config(config)
    output = tmp_path / "capdep-config"

    result = setup_daily_driver(
        config_path=config,
        output_dir=output,
        self_addresses="me@example.com",
        trusted_draft_recipients="assistant@example.com",
    )

    assert result.apply is False
    assert result.status == "dry_run"
    assert result.details["readiness"]["ready"] is True
    assert "me@example.com" in result.details["relationship_groups_yaml"]
    assert not output.exists()


def test_setup_daily_driver_apply_writes_only_requested_output_dir(tmp_path: Path) -> None:
    config = tmp_path / "daemon.yaml"
    _write_daemon_config(config)
    output = tmp_path / "capdep-config"

    result = setup_daily_driver(
        apply=True,
        config_path=config,
        output_dir=output,
        self_addresses="me@example.com,marc@example.com",
        trusted_draft_recipients="trusted@example.com",
        family_recipients="family@example.com",
        work_recipients="work@example.com",
    )

    relationships = output / "relationship_groups.yaml"
    patterns = output / "approval-patterns.yaml"
    assert result.status == "configured"
    assert relationships.is_file()
    assert patterns.is_file()
    assert "marc@example.com" in relationships.read_text(encoding="utf-8")
    assert "trusted@example.com" in patterns.read_text(encoding="utf-8")


def test_capdep_setup_daily_driver_cli_json(tmp_path: Path) -> None:
    config = tmp_path / "daemon.yaml"
    _write_daemon_config(config)
    output = tmp_path / "out"

    result = runner.invoke(
        setup_app,
        [
            "daily-driver",
            "--config",
            str(config),
            "--output-dir",
            str(output),
            "--self",
            "me@example.com",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["domain"] == "daily-driver"
    assert payload["apply"] is False
    assert payload["details"]["readiness"]["schema"] == "capdep.daily_driver_readiness.v1"
    assert not output.exists()
