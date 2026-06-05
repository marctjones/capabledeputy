"""Tests for ISOLATION_REGION_CREATED / _DISCARDED emission (FR-040).

The events were defined in `audit/events.py` but never emitted. This
closes the gap so Pattern ⑤'s region lifecycle is auditable:
- A successful run produces exactly one CREATED followed by one DISCARDED
- A failed execute() still produces a DISCARDED (reason='execute_failed')
- When audit=None (e.g. tests), no events are emitted
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from capabledeputy.audit.events import EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.substrate.in_process_sandbox import InProcessSandboxActuator
from capabledeputy.substrate.sandbox_actuator import (
    SandboxOutputFile,
    SandboxResult,
)
from capabledeputy.tools.native.sandbox import make_sandbox_tools
from capabledeputy.tools.registry import ToolContext


def _read_events(audit_path: Path) -> list[dict]:
    out: list[dict] = []
    if not audit_path.is_file():
        return out
    for line in audit_path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _events_of_type(events: list[dict], et: str) -> list[dict]:
    return [e for e in events if e.get("event_type") == et]


class _FakePolicyContext:
    """Minimum stub for make_sandbox_tools() — only the
    sandbox_actuator attribute is consulted."""

    def __init__(self, actuator):
        self.sandbox_actuator = actuator


async def test_successful_run_emits_created_then_discarded(tmp_path: Path) -> None:
    """A normal sandbox.run produces CREATED + DISCARDED in order."""
    audit_path = tmp_path / "audit.jsonl"
    audit = AuditWriter(audit_path)
    actuator = InProcessSandboxActuator()
    tools = make_sandbox_tools(_FakePolicyContext(actuator), audit=audit)
    assert len(tools) == 1
    handler = tools[0].handler

    ctx = ToolContext(session_id=uuid4(), label_set=frozenset())
    result = await handler(
        {"spec_id": "scratch", "argv": ["echo", "hi"], "timeout_seconds": 5},
        ctx,
    )
    assert "error" not in result.output

    events = _read_events(audit_path)
    created = _events_of_type(events, EventType.ISOLATION_REGION_CREATED.value)
    discarded = _events_of_type(events, EventType.ISOLATION_REGION_DISCARDED.value)

    assert len(created) == 1
    assert len(discarded) == 1
    # Same region_id on both events — region lifecycle is paired
    assert created[0]["payload"]["region_id"] == discarded[0]["payload"]["region_id"]
    assert created[0]["payload"]["spec_id"] == "scratch"
    assert discarded[0]["payload"]["reason"] == "run_completed"


async def test_failed_execute_still_emits_discarded(tmp_path: Path) -> None:
    """When execute() raises, the region is discarded best-effort AND
    the DISCARDED event still fires with reason='execute_failed'."""
    audit_path = tmp_path / "audit.jsonl"
    audit = AuditWriter(audit_path)

    class _RaisingActuator(InProcessSandboxActuator):
        def execute(self, **kwargs):
            raise RuntimeError("simulated execute failure")

    actuator = _RaisingActuator()
    tools = make_sandbox_tools(_FakePolicyContext(actuator), audit=audit)
    handler = tools[0].handler

    ctx = ToolContext(session_id=uuid4(), label_set=frozenset())
    result = await handler(
        {"spec_id": "scratch", "argv": ["true"], "timeout_seconds": 5},
        ctx,
    )
    # The handler returns the error in output, not a raise
    assert "error" in result.output
    assert "execute failed" in result.output["error"]

    events = _read_events(audit_path)
    discarded = _events_of_type(events, EventType.ISOLATION_REGION_DISCARDED.value)
    assert len(discarded) == 1
    assert discarded[0]["payload"]["reason"] == "execute_failed"
    assert "simulated execute failure" in discarded[0]["payload"]["error"]


async def test_audit_none_emits_nothing(tmp_path: Path) -> None:
    """When `audit=None` (legacy / test path), no events are emitted
    and the sandbox tool still works normally — no NoneType crashes."""
    actuator = InProcessSandboxActuator()
    tools = make_sandbox_tools(_FakePolicyContext(actuator), audit=None)
    handler = tools[0].handler

    ctx = ToolContext(session_id=uuid4(), label_set=frozenset())
    result = await handler(
        {"spec_id": "scratch", "argv": ["echo", "hello"], "timeout_seconds": 5},
        ctx,
    )
    assert "error" not in result.output
