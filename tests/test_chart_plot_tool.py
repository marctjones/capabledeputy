"""Tests for the native chart.plot tool."""

from __future__ import annotations

import pytest

from capabledeputy.daemon.image_attachments import image_attachment_payloads_from_outcome
from capabledeputy.tools.native.chart import make_chart_tools
from capabledeputy.tools.registry import ToolContext
from uuid import uuid4


@pytest.fixture
def chart_tool():
    return make_chart_tools()[0]


@pytest.mark.asyncio
async def test_chart_plot_writes_png_and_markdown(chart_tool, tmp_path, monkeypatch) -> None:
    pytest.importorskip("matplotlib")
    monkeypatch.setattr(
        "capabledeputy.tools.native.chart._CHART_DIR",
        tmp_path / "charts",
    )
    monkeypatch.setattr(
        "capabledeputy.tools.native.chart._POLICY_TARGET",
        str(tmp_path / "charts" / "plot.png"),
    )

    ctx = ToolContext(session_id=uuid4(), label_state=None)  # type: ignore[arg-type]
    result = await chart_tool.handler(
        {
            "chart_type": "line",
            "title": "US population by decade",
            "x": ["1900s", "2000s", "2010s"],
            "y": [76, 278, 321],
            "ylabel": "Millions",
            "alt": "US population growth",
        },
        ctx,
    )
    out = result.output
    assert out["ok"] is True
    path = out["plot"]
    assert path.endswith(".png")
    assert "![US population growth]" in out["markdown"]
    assert (tmp_path / "charts").exists()

    payloads = image_attachment_payloads_from_outcome({"output": out})
    assert any(item["path"] == path for item in payloads)


@pytest.mark.asyncio
async def test_chart_plot_rejects_mismatched_axes(chart_tool) -> None:
    ctx = ToolContext(session_id=uuid4(), label_state=None)  # type: ignore[arg-type]
    result = await chart_tool.handler({"x": ["a"], "y": [1, 2]}, ctx)
    assert result.output["ok"] is False