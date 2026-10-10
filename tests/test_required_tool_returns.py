import json
from typing import Literal

import pytest

from capabledeputy.agent.loop import run_turn
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import LLMResponse, ToolCall
from capabledeputy.mode.dispatcher import ExecutionMode
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.policy.labels import CategoryTag, LabelState
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier
from capabledeputy.session.graph import SessionGraph
from capabledeputy.tools.client import LabeledToolClient
from capabledeputy.tools.registry import ToolDefinition, ToolRegistry, ToolResult


@pytest.mark.parametrize("failure", [False, True])
async def test_blind_requirement_masks_values_links_and_errors(tmp_path, failure):
    calls = []
    source = LabelState(
        a=frozenset(
            {CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared")}
        )
    )

    async def handler(args, context):
        calls.append(args.copy())
        args["returned_secret"] = "PRIVATE_SOURCE_VALUE"
        if failure:
            raise RuntimeError("PRIVATE_SOURCE_VALUE")
        return ToolResult(
            output={
                "value": "PRIVATE_SOURCE_VALUE",
                "resource_link": "private://secret",
                "nested": {"preview": "PRIVATE_SOURCE_VALUE"},
            },
            additional_tags=source,
        )

    graph = SessionGraph()
    session = await graph.new(required_modes=frozenset({"reference", "sealed"}))
    await graph.grant_capability(session.id, Capability(kind=CapabilityKind.READ_FS, pattern="*"))
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="read",
            description="test",
            capability_kind=CapabilityKind.READ_FS,
            handler=handler,
            operations=(Operation(EffectClass.FETCH),),
            risk_ids=("RISK-INDIRECT-INJECTION",),
        )
    )
    client = LabeledToolClient(registry, graph, AuditWriter(tmp_path / "audit.jsonl"))
    outcome = await client.call_tool(session.id, "read", {"target": "local"})
    assert outcome.decision == Decision.ALLOW
    assert outcome.result_withheld
    assert "PRIVATE_SOURCE_VALUE" not in str(outcome)
    assert "private://secret" not in json.dumps(outcome.output)
    assert len(calls) == 1
    if not failure:
        assert graph.get(session.id).label_state == source
        llm = FakeLLMClient(
            [
                LLMResponse(
                    content="",
                    tool_calls=(ToolCall(id="c1", name="read", args={"target": "local"}),),
                ),
                LLMResponse(content="Result withheld; review separately."),
            ]
        )
        await run_turn(
            session_id=session.id,
            user_message="process without showing values",
            graph=graph,
            registry=registry,
            tool_client=client,
            llm=llm,
            audit=AuditWriter(tmp_path / "loop-audit.jsonl"),
            force_mode=ExecutionMode.REFERENCE,
        )
        assert len(calls) == 2  # one direct call, one requested loop call; no automatic retry
        assert "PRIVATE_SOURCE_VALUE" not in str(llm.calls)
        assert "PRIVATE_SOURCE_VALUE" not in str(graph.get(session.id).history)
        internal = await client.call_tool(
            session.id,
            "read",
            {"target": "local"},
            result_recipient="internal",
        )
        assert not internal.result_withheld
        assert internal.output is not None
        assert internal.output["value"] == "PRIVATE_SOURCE_VALUE"
        assert graph.get(session.id).label_state == source


async def test_empty_requirement_denies_even_internal_dispatch(tmp_path):
    graph = SessionGraph()
    s = await graph.new(required_modes=frozenset())
    client = LabeledToolClient(ToolRegistry(), graph, AuditWriter(tmp_path / "audit.jsonl"))
    recipients: tuple[Literal["planner", "internal"], ...] = ("planner", "internal")
    for recipient in recipients:
        outcome = await client.call_tool(s.id, "not-registered", {}, result_recipient=recipient)
        assert outcome.decision == Decision.DENY
        assert outcome.rule == "required-handling-empty"
