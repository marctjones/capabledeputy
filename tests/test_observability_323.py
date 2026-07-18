"""#323 — live telemetry: gauge metrics (approval queue depth, upstream health),
the dependency-free structured JSON log, and OTLP-metrics file export."""

from __future__ import annotations

import io
import json
from pathlib import Path
from uuid import uuid4

import pytest

from capabledeputy.approval.model import ApprovalAction
from capabledeputy.approval.queue import ApprovalQueue
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.observability import (
    StructuredLogger,
    get_metrics,
    log_event,
    reset_logger,
    reset_metrics,
    set_logger,
    snapshot_to_otlp,
    write_otlp_metrics,
)
from capabledeputy.policy.labels import LabelState


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_metrics()
    yield
    reset_metrics()
    reset_logger()


# --- gauges ------------------------------------------------------------------


def test_gauge_is_last_write_wins_and_in_snapshot() -> None:
    m = get_metrics()
    m.set_gauge("q.depth", 3)
    m.set_gauge("q.depth", 1)  # a gauge can go DOWN, unlike a counter
    snap = m.snapshot()
    assert snap.gauges["q.depth"] == 1
    assert "gauges" in snap.as_dict()
    assert snap.as_dict()["gauges"]["q.depth"] == 1


# --- structured log ----------------------------------------------------------


def _cap_logger(fmt: str = "json", level: str = "info") -> tuple[StructuredLogger, io.StringIO]:
    buf = io.StringIO()
    logger = StructuredLogger(stream=buf, fmt=fmt, level=level)
    set_logger(logger)
    return logger, buf


def test_log_event_emits_stable_json() -> None:
    _, buf = _cap_logger()
    log_event("warning", "approval.queued", approval_id=7, queue_depth=2)
    rec = json.loads(buf.getvalue().strip())
    assert rec["level"] == "warning"
    assert rec["event"] == "approval.queued"
    assert rec["approval_id"] == 7
    assert rec["queue_depth"] == 2
    assert "ts" in rec


def test_log_level_floor_drops_below_threshold() -> None:
    _, buf = _cap_logger(level="warning")
    log_event("info", "chatty")  # below floor -> dropped
    log_event("error", "boom")
    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["event"] == "boom"


def test_log_format_off_is_silent() -> None:
    _, buf = _cap_logger(fmt="off")
    log_event("error", "boom")
    assert buf.getvalue() == ""


def test_log_text_format_is_human_readable() -> None:
    _, buf = _cap_logger(fmt="text")
    log_event("info", "daemon.started", sessions_loaded=4)
    line = buf.getvalue().strip()
    assert "[INFO]" in line
    assert "daemon.started" in line
    assert "sessions_loaded=4" in line


def test_log_serializes_awkward_fields_without_crashing() -> None:
    _, buf = _cap_logger()
    log_event("info", "x", where=Path("/tmp/a"), who=uuid4())  # Path + UUID
    rec = json.loads(buf.getvalue().strip())
    assert rec["where"] == "/tmp/a"


# --- OTLP export -------------------------------------------------------------


def test_snapshot_to_otlp_maps_all_three_kinds() -> None:
    m = get_metrics()
    m.incr("turns", 5)
    m.set_gauge("approval.queue_depth", 2)
    m.observe("turn.latency", 0.1)
    m.observe("turn.latency", 0.2)
    payload = snapshot_to_otlp(m.snapshot(), time_unix_nano=123)
    metrics = payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]
    by_name = {x["name"]: x for x in metrics}
    assert by_name["turns"]["sum"]["dataPoints"][0]["asInt"] == "5"
    assert by_name["approval.queue_depth"]["gauge"]["dataPoints"][0]["asDouble"] == 2
    quantiles = by_name["turn.latency"]["summary"]["dataPoints"][0]["quantileValues"]
    assert {q["quantile"] for q in quantiles} == {0.5, 0.95, 0.99, 1.0}


def test_write_otlp_metrics_writes_valid_json(tmp_path: Path) -> None:
    m = get_metrics()
    m.incr("errors", 1)
    out = write_otlp_metrics(m.snapshot(), tmp_path / "metrics.json", time_unix_nano=7)
    assert out.is_file()
    json.loads(out.read_text())  # parseable


# --- integration: approval queue depth gauge ---------------------------------


async def test_approval_queue_depth_gauge_tracks_pending(tmp_path: Path) -> None:
    _, buf = _cap_logger()
    queue = ApprovalQueue(audit=AuditWriter(tmp_path / "audit.jsonl"))
    sid = uuid4()
    a = await queue.submit(
        from_session=sid,
        action=ApprovalAction.DECLASSIFY,
        payload="x",
        target="y",
        labels_in=LabelState(),
    )
    await queue.submit(
        from_session=sid,
        action=ApprovalAction.DECLASSIFY,
        payload="x2",
        target="y2",
        labels_in=LabelState(),
    )
    assert get_metrics().snapshot().gauges["approval.queue_depth"] == 2
    # A structured approval.queued line was emitted for the submit.
    events = [json.loads(ln)["event"] for ln in buf.getvalue().splitlines() if ln.strip()]
    assert "approval.queued" in events

    await queue.approve(a.id, decided_by="user")
    assert get_metrics().snapshot().gauges["approval.queue_depth"] == 1  # drained by one


# --- integration: upstream health gauge --------------------------------------


def test_upstream_health_gauge_counts_registered_vs_total() -> None:
    from capabledeputy.tools.registry import ToolRegistry
    from capabledeputy.upstream.manager import UpstreamManager, UpstreamServerStatus

    mgr = UpstreamManager([], ToolRegistry())
    mgr._status = {
        "ok": UpstreamServerStatus(name="ok", state="registered", registered_at_epoch=0),
        "dead": UpstreamServerStatus(name="dead", state="failed", registered_at_epoch=0),
    }
    mgr._record_health()
    g = get_metrics().snapshot().gauges
    assert g["upstream.servers_total"] == 2
    assert g["upstream.servers_healthy"] == 1  # one failed -> observable
