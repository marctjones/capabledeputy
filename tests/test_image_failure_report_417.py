"""#417 — when the image tool RAN and failed (ok:False), the turn must report
the backend/model's actual error faithfully, not a generic or moralizing
paraphrase. Pins the pure extractor the loop uses.
"""

from __future__ import annotations

from capabledeputy.agent.chat_turn import image_generation_failure_report_from_outcomes
from capabledeputy.policy.rules import Decision
from capabledeputy.tools.client import ToolCallOutcome

_IMG = "bundled-image-generate.image.generate"


def _outcome(
    tool_name: str,
    output: dict | None,
    decision: Decision = Decision.ALLOW,
) -> ToolCallOutcome:
    return ToolCallOutcome(decision=decision, output=output, tool_name=tool_name)


def test_failed_image_outcome_reports_backend_error_verbatim() -> None:
    out = [_outcome(_IMG, {"ok": False, "error": "MFLUX requires MLX/Metal on Apple Silicon"})]
    report = image_generation_failure_report_from_outcomes(out)
    assert report is not None
    assert "MFLUX requires MLX/Metal on Apple Silicon" in report
    # No moralizing / generic filler.
    assert "appropriate" not in report.lower()
    assert "there was an issue" not in report.lower()


def test_failed_image_outcome_without_detail_still_reports() -> None:
    report = image_generation_failure_report_from_outcomes([_outcome(_IMG, {"ok": False})])
    assert report is not None
    assert "no detail" in report.lower()


def test_successful_image_outcome_returns_none() -> None:
    out = [_outcome(_IMG, {"ok": True, "markdown": "![x](/p.png)"})]
    assert image_generation_failure_report_from_outcomes(out) is None


def test_non_image_failure_is_ignored() -> None:
    out = [_outcome("fs.read", {"ok": False, "error": "denied"})]
    assert image_generation_failure_report_from_outcomes(out) is None


def test_denied_image_outcome_is_ignored() -> None:
    # A non-ALLOW decision is handled by the approval/deny path, not here.
    out = [_outcome(_IMG, {"ok": False, "error": "x"}, decision=Decision.DENY)]
    assert image_generation_failure_report_from_outcomes(out) is None


def test_empty_outcomes_returns_none() -> None:
    assert image_generation_failure_report_from_outcomes([]) is None
