from __future__ import annotations

import json
from typing import Any

from typer.testing import CliRunner

from capabledeputy.cli.scripting import scripting_app


def test_scripting_run_artifact_cli_sends_sandbox_evidence(
    tmp_path,
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_print_result(
        method: str,
        params: dict[str, Any],
        *,
        json_output: bool,
    ) -> None:
        captured["method"] = method
        captured["params"] = params
        captured["json_output"] = json_output

    monkeypatch.setattr("capabledeputy.cli.scripting._print_result", fake_print_result)
    result_path = tmp_path / "run.json"
    result_path.write_text(
        json.dumps(
            {
                "spec_id": "run-1",
                "exit_code": 0,
                "output_digest": "sha256:abc",
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        scripting_app,
        [
            "run-artifact",
            str(result_path),
            "--workspace-root",
            str(tmp_path),
            "--workspace-id",
            "photos",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert captured["method"] == "scripting.run_artifact"
    assert captured["params"]["workspace_id"] == "photos"
    assert captured["params"]["run_result"]["exit_code"] == 0
    assert captured["json_output"] is True
