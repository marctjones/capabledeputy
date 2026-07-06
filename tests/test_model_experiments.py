from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from capabledeputy.model_experiments import (
    candidates_for_purpose,
    experiment_plan,
    write_jsonl_plan,
)


def test_model_experiment_purpose_shortlists_are_ranked() -> None:
    tool = candidates_for_purpose("tool_selection")
    guard = candidates_for_purpose("risk_guard")
    reranker = candidates_for_purpose("reranker")

    assert [candidate.candidate_id for candidate in tool] == [
        "xlam-8b-tool",
        "xlam-32b-tool",
    ]
    assert guard[0].candidate_id == "qwen3guard-06b"
    assert reranker[0].candidate_id == "bge-reranker-v2-m3"


def test_model_experiment_conversion_commands_are_explicit(tmp_path: Path) -> None:
    plan = experiment_plan(purpose="tool_selection", output_root=tmp_path / "converted")
    commands = {
        candidate["candidate_id"]: candidate["conversion_command"]
        for candidate in plan["candidates"]
    }

    assert commands["xlam-8b-tool"][1:3] == ["-m", "mlx_lm.convert"]
    assert "--hf-path" in commands["xlam-8b-tool"]
    assert "Salesforce/Llama-xLAM-2-8b-fc-r" in commands["xlam-8b-tool"]
    assert str(tmp_path / "converted" / "xlam-8b-tool") in commands["xlam-8b-tool"]


def test_model_experiment_rerankers_do_not_claim_mlx_lm_conversion(tmp_path: Path) -> None:
    plan = experiment_plan(purpose="reranker", output_root=tmp_path / "converted")

    assert {candidate["conversion_status"] for candidate in plan["candidates"]} == {
        "separate_runtime"
    }
    assert all(candidate["conversion_command"] is None for candidate in plan["candidates"])


def test_write_jsonl_plan_includes_cases(tmp_path: Path) -> None:
    results = tmp_path / "plan.jsonl"
    plan = experiment_plan(purpose="risk_guard", output_root=tmp_path / "converted")

    write_jsonl_plan(plan, results)

    rows = [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["event"] == "experiment_plan"
    assert any(
        row["event"] == "candidate" and row["candidate_id"] == "qwen3guard-06b"
        for row in rows
    )
    assert any(
        row["event"] == "case" and row["case_id"] == "external_prompt_injection"
        for row in rows
    )


def test_model_experiment_script_writes_filtered_plan(tmp_path: Path) -> None:
    results = tmp_path / "tool-plan.jsonl"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/benchmark_model_experiments.py",
            "--purpose",
            "tool_selection",
            "--results",
            str(results),
            "--output-root",
            str(tmp_path / "converted"),
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    assert "wrote_plan" in completed.stdout
    rows = [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["purpose"] == "tool_selection"
    assert {row["purpose"] for row in rows if row["event"] == "candidate"} == {
        "tool_selection"
    }
