"""Tests for the quarantined LLM extractor + dual-LLM mode tool.

Uses FakeLLMClient with scripted JSON outputs so we can pin the exact
quarantined LLM behavior. The architectural property under test: the
planner LLM never sees the raw labeled text, only the schema-validated
extracted object.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from capabledeputy.app import App
from capabledeputy.daemon.agent_handlers import make_agent_handlers
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import FinishReason, LLMResponse, ToolCall
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import CategoryTag, LabelState
from capabledeputy.policy.tiers import Tier
from capabledeputy.quarantined.extractor import ExtractionError, extract
from capabledeputy.quarantined.schemas import (
    DoseSummary,
    list_schemas,
)


async def test_extract_validates_against_schema() -> None:
    fake = FakeLLMClient(
        [
            LLMResponse(
                content=json.dumps(
                    {
                        "medication_name": "lisinopril",
                        "dosage_mg": 10,
                        "frequency": "daily",
                    },
                ),
                finish_reason=FinishReason.STOP,
            ),
        ],
    )
    result = await extract(fake, "DoseSummary", "Take 10mg lisinopril once daily.")
    assert isinstance(result, DoseSummary)
    assert result.medication_name == "lisinopril"
    assert result.dosage_mg == 10
    assert result.frequency == "daily"


async def test_extract_strips_markdown_fences() -> None:
    fake = FakeLLMClient(
        [
            LLMResponse(
                content=(
                    "```json\n"
                    + json.dumps(
                        {
                            "medication_name": "lisinopril",
                            "dosage_mg": 10,
                            "frequency": "daily",
                        },
                    )
                    + "\n```"
                ),
                finish_reason=FinishReason.STOP,
            ),
        ],
    )
    result = await extract(fake, "DoseSummary", "raw text")
    assert isinstance(result, DoseSummary)


async def test_extract_rejects_invalid_json() -> None:
    fake = FakeLLMClient(
        [LLMResponse(content="not valid json", finish_reason=FinishReason.STOP)],
    )
    with pytest.raises(ExtractionError, match="not JSON"):
        await extract(fake, "DoseSummary", "x")


async def test_extract_rejects_schema_violation() -> None:
    fake = FakeLLMClient(
        [
            LLMResponse(
                content=json.dumps({"medication_name": "x"}),
                finish_reason=FinishReason.STOP,
            ),
        ],
    )
    with pytest.raises(ExtractionError, match="schema validation"):
        await extract(fake, "DoseSummary", "x")


async def test_extract_rejects_quarantined_tool_calls() -> None:
    fake = FakeLLMClient(
        [
            LLMResponse(
                content="",
                tool_calls=(ToolCall(id="1", name="x", args={}),),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
        ],
    )
    with pytest.raises(ExtractionError, match="must not have access"):
        await extract(fake, "DoseSummary", "x")


async def test_unknown_schema_raises() -> None:
    fake = FakeLLMClient([LLMResponse(content="{}", finish_reason=FinishReason.STOP)])
    with pytest.raises(KeyError):
        await extract(fake, "DoesNotExist", "x")


def test_list_schemas_includes_starter_set() -> None:
    schemas = list_schemas()
    assert "DoseSummary" in schemas
    assert "FinancialSummary" in schemas
    assert "ContactInfo" in schemas


async def test_extract_tool_returns_data_without_label_propagation(
    tmp_path: Path,
) -> None:
    quarantined = FakeLLMClient(
        [
            LLMResponse(
                content=json.dumps(
                    {
                        "medication_name": "lisinopril",
                        "dosage_mg": 10,
                        "frequency": "daily",
                    },
                ),
                finish_reason=FinishReason.STOP,
            ),
        ],
    )

    planner = FakeLLMClient(
        [
            LLMResponse(
                content="",
                tool_calls=(
                    ToolCall(
                        id="x1",
                        name="quarantined.extract",
                        args={"key": "rx", "schema": "DoseSummary"},
                    ),
                ),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            LLMResponse(
                content="The dose is 10mg lisinopril once daily.",
                finish_reason=FinishReason.STOP,
            ),
        ],
    )

    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=planner,
        quarantined_llm=quarantined,
    )
    await app.startup()

    app.memory.write(
        "rx",
        "Patient prescription: take 10mg lisinopril once daily for HTN. BP=120/80.",
        LabelState(
            a=frozenset(
                {CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared")}
            )
        ),
    )

    s = await app.graph.new()
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))

    handlers = make_agent_handlers(app)
    result = await handlers["session.send"](
        {
            "session_id": str(s.id),
            "message": "Extract the dose information from memory key 'rx'.",
        },
    )

    assert result["tool_outcomes"][0]["decision"] == "allow"
    # tags_added should be empty/absent: quarantined extraction doesn't
    # propagate labels (the field may be omitted when empty).
    final = app.graph.get(s.id)
    assert not any(c.category == "health" for c in final.label_state.a)

    output = result["tool_outcomes"][0]["output"]
    assert output["data"]["medication_name"] == "lisinopril"
    assert output["data"]["dosage_mg"] == 10

    planner_messages_seen = []
    for msgs, _tools in planner.calls:
        for m in msgs:
            planner_messages_seen.append(m.content)
    full_planner_context = "\n".join(planner_messages_seen)
    assert "BP=120/80" not in full_planner_context
    assert "for HTN" not in full_planner_context
