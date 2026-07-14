"""Opt-in real model smoke matrix.

These tests are collected by the standard suite but skipped unless explicitly
enabled. They are intentionally broad contract smokes rather than exact-output
tests because real model behavior is nondeterministic.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from capabledeputy.app import App
from capabledeputy.daemon.agent_handlers import make_agent_handlers
from capabledeputy.llm.litellm_client import LiteLLMClient
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import LabelState

pytestmark = pytest.mark.real_model


_ENABLED = pytest.mark.skipif(
    os.environ.get("CAPDEP_REAL_MODEL_SMOKE") != "1",
    reason="set CAPDEP_REAL_MODEL_SMOKE=1 and provider credentials to run real model smokes",
)


def _model_names() -> list[str]:
    raw = os.environ.get("CAPDEP_REAL_MODEL_MATRIX", "claude-haiku-4-5")
    return [item.strip() for item in raw.split(",") if item.strip()]


@_ENABLED
@pytest.mark.parametrize("model_name", _model_names())
async def test_real_model_can_complete_safe_chat_turn(
    tmp_path: Path,
    model_name: str,
) -> None:
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=LiteLLMClient(model=model_name),
    )
    await app.startup()
    session = await app.graph.new(intent=f"real model smoke {model_name}")
    handlers = make_agent_handlers(app)

    result = await handlers["session.send"](
        {
            "session_id": str(session.id),
            "message": "Reply with one short sentence confirming the smoke test is running.",
            "max_iterations": 2,
        },
    )

    assert result["content"].strip()
    assert result["iterations"] >= 1


@_ENABLED
@pytest.mark.parametrize("model_name", _model_names())
async def test_real_model_can_dispatch_safe_memory_tool(
    tmp_path: Path,
    model_name: str,
) -> None:
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=LiteLLMClient(model=model_name),
    )
    await app.startup()
    app.memory.write("smoke-note", "CapDep real model MCP/tool smoke test data.", LabelState())
    session = await app.graph.new(intent=f"real model tool smoke {model_name}")
    app.graph._sessions[session.id] = replace(
        session,
        capability_set=frozenset({Capability(kind=CapabilityKind.READ_FS, pattern="*")}),
    )
    handlers = make_agent_handlers(app)

    result = await handlers["session.send"](
        {
            "session_id": str(session.id),
            "message": (
                "Read memory key 'smoke-note' with the memory.read tool, then summarize it."
            ),
            "max_iterations": 4,
        },
    )

    assert result["content"].strip()
    assert any(
        outcome.get("tool_name", "").endswith("memory.read") for outcome in result["tool_outcomes"]
    )
