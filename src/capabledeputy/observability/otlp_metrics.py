"""#323 — render the in-process metrics snapshot as OTLP metrics JSON.

This is the FILE export path (mirrors `compliance/otlp.py`, which exports audit
events as OTLP traces JSON). It deliberately does NOT open a network connection
or import the opentelemetry SDK — a live OTLP/gRPC push would put a network
client in the trust boundary. A collector picks the file up out-of-band, or a
future opt-in shipper reads it; the daemon itself only writes bytes.

Mapping to the OTLP metrics data model:
  counters  → Sum (monotonic, cumulative)
  gauges    → Gauge
  histogram → Summary (quantileValues from the retained p50/p95/p99 + max@1.0;
              `sum` is 0.0 — the snapshot keeps percentiles, not the running sum)
"""

from __future__ import annotations

import json
from pathlib import Path

from capabledeputy.observability.metrics import MetricsSnapshot


def _sum_metric(name: str, value: int, ts: str) -> dict:
    return {
        "name": name,
        "sum": {
            "aggregationTemporality": 2,  # CUMULATIVE
            "isMonotonic": True,
            "dataPoints": [{"asInt": str(value), "timeUnixNano": ts}],
        },
    }


def _gauge_metric(name: str, value: float, ts: str) -> dict:
    return {
        "name": name,
        "gauge": {"dataPoints": [{"asDouble": value, "timeUnixNano": ts}]},
    }


def _summary_metric(name: str, count: int, quantiles: dict[float, float], ts: str) -> dict:
    return {
        "name": name,
        "unit": "s",
        "summary": {
            "dataPoints": [
                {
                    "count": str(count),
                    "sum": 0.0,  # not tracked — the snapshot keeps percentiles
                    "quantileValues": [
                        {"quantile": q, "value": v} for q, v in sorted(quantiles.items())
                    ],
                    "timeUnixNano": ts,
                }
            ]
        },
    }


def snapshot_to_otlp(
    snapshot: MetricsSnapshot,
    *,
    service_name: str = "capabledeputy",
    time_unix_nano: int = 0,
) -> dict:
    """OTLP `ExportMetricsServiceRequest`-shaped dict for `snapshot`.

    `time_unix_nano` stamps every data point; pass a real timestamp at the call
    site (kept as a parameter so this stays pure/testable)."""
    ts = str(time_unix_nano)
    metrics: list[dict] = []
    for name, value in sorted(snapshot.counters.items()):
        metrics.append(_sum_metric(name, value, ts))
    for name, gvalue in sorted(snapshot.gauges.items()):
        metrics.append(_gauge_metric(name, gvalue, ts))
    for name, h in sorted(snapshot.histograms.items()):
        metrics.append(
            _summary_metric(name, h.count, {0.5: h.p50, 0.95: h.p95, 0.99: h.p99, 1.0: h.max}, ts)
        )
    return {
        "resourceMetrics": [
            {
                "resource": {
                    "attributes": [{"key": "service.name", "value": {"stringValue": service_name}}]
                },
                "scopeMetrics": [
                    {
                        "scope": {"name": "capabledeputy.observability"},
                        "metrics": metrics,
                    }
                ],
            }
        ]
    }


def write_otlp_metrics(
    snapshot: MetricsSnapshot,
    path: Path,
    *,
    service_name: str = "capabledeputy",
    time_unix_nano: int = 0,
) -> Path:
    """Write `snapshot` as OTLP metrics JSON to `path`; returns the path."""
    payload = snapshot_to_otlp(snapshot, service_name=service_name, time_unix_nano=time_unix_nano)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
