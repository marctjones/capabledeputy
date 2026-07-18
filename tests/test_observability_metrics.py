"""#323 — in-process metrics registry: counters, latency histograms with
deterministic percentiles, the timer context manager, and the CLI snapshot."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from capabledeputy.cli.main import app
from capabledeputy.observability import Metrics, get_metrics, reset_metrics
from capabledeputy.observability.metrics import _percentile

runner = CliRunner()


def test_counters_accumulate() -> None:
    m = Metrics()
    m.incr("turns")
    m.incr("turns", 4)
    assert m.snapshot().counters["turns"] == 5


def test_histogram_percentiles_are_deterministic() -> None:
    m = Metrics()
    for v in range(1, 101):  # 1..100
        m.observe("lat", float(v))
    h = m.snapshot().histograms["lat"]
    assert h.count == 100
    assert h.max == 100.0
    assert 49 <= h.p50 <= 52
    assert 94 <= h.p95 <= 96
    assert 98 <= h.p99 <= 100


def test_percentile_helper_edges() -> None:
    assert _percentile([], 0.5) == 0.0
    assert _percentile([7.0], 0.99) == 7.0


def test_timer_records_and_counts_errors() -> None:
    m = Metrics()
    with m.timer("op", error_counter="op.errors"):
        pass
    assert m.snapshot().histograms["op"].count == 1

    with pytest.raises(ValueError), m.timer("op", error_counter="op.errors"):
        raise ValueError("boom")
    snap = m.snapshot()
    assert snap.counters["op.errors"] == 1
    assert snap.histograms["op"].count == 2  # timed even on error


def test_histogram_is_memory_bounded() -> None:
    from capabledeputy.observability.metrics import _MAX_SAMPLES

    m = Metrics()
    for i in range(_MAX_SAMPLES + 500):
        m.observe("x", float(i))
    assert m.snapshot().histograms["x"].count == _MAX_SAMPLES


def test_global_registry_reset() -> None:
    reset_metrics()
    get_metrics().incr("g")
    assert get_metrics().snapshot().counters == {"g": 1}
    reset_metrics()
    assert get_metrics().snapshot().counters == {}


def test_metrics_cli_json_and_empty() -> None:
    reset_metrics()
    result = runner.invoke(app, ["metrics", "--json"])
    assert result.exit_code == 0
    assert "counters" in result.stdout

    get_metrics().incr("turns", 3)
    result = runner.invoke(app, ["metrics"])
    assert result.exit_code == 0
    assert "turns" in result.stdout
