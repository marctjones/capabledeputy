"""OTLP JSON exporter for CapableDeputy audit events."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _hex_id(seed: str, length: int) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:length]


def _time_unix_nano(value: Any) -> str:
    if not value:
        return "0"
    try:
        normalized = str(value).replace("Z", "+00:00")
        return str(int(datetime.fromisoformat(normalized).timestamp() * 1_000_000_000))
    except ValueError:
        return "0"


def _attr(key: str, value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    return {"key": key, "value": {"stringValue": "" if value is None else str(value)}}


def audit_events_to_otlp_traces(
    audit_events: list[dict[str, Any]],
    *,
    service_name: str = "capabledeputy",
) -> dict[str, Any]:
    spans: list[dict[str, Any]] = []
    for index, event in enumerate(audit_events):
        audit_id = str(event.get("audit_id") or f"event-{index}")
        event_type = str(event.get("event_type") or "unknown")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        attrs = [
            _attr("capdep.audit_id", audit_id),
            _attr("capdep.event_type", event_type),
            _attr("capdep.session_id", event.get("session_id")),
            _attr("capdep.turn_id", event.get("turn_id")),
            _attr("capdep.step_id", event.get("step_id")),
        ]
        for key in ("rule", "decision", "tool", "region_id", "spec_id", "reason"):
            if key in payload:
                attrs.append(_attr(f"capdep.{key}", payload.get(key)))
        timestamp = _time_unix_nano(event.get("timestamp"))
        spans.append(
            {
                "traceId": _hex_id(str(event.get("session_id") or audit_id), 32),
                "spanId": _hex_id(audit_id, 16),
                "name": event_type,
                "kind": 1,
                "startTimeUnixNano": timestamp,
                "endTimeUnixNano": timestamp,
                "attributes": attrs,
            },
        )
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        _attr("service.name", service_name),
                        _attr("telemetry.sdk.name", "capabledeputy"),
                    ],
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "capabledeputy.audit"},
                        "spans": spans,
                    },
                ],
            },
        ],
    }


def emit_otlp_traces_json(
    output_path: Path,
    audit_events: list[dict[str, Any]],
    *,
    service_name: str = "capabledeputy",
) -> None:
    payload = audit_events_to_otlp_traces(audit_events, service_name=service_name)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
