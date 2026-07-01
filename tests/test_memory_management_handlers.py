from __future__ import annotations

from pathlib import Path
from uuid import UUID

from capabledeputy.app import App
from capabledeputy.daemon.memory_handlers import make_memory_handlers
from capabledeputy.daemon.session_handlers import make_session_handlers
from capabledeputy.llm.types import FinishReason, LLMResponse
from capabledeputy.session.model import Turn
from tests.daemon_integration import running_daemon


async def test_memory_policy_prune_and_compact_session(tmp_path: Path) -> None:
    app = App(state_db_path=tmp_path / "state.db", audit_log_path=tmp_path / "audit.jsonl")
    await app.startup()
    handlers = make_memory_handlers(app)
    session_handlers = make_session_handlers(app.graph)
    created = await session_handlers["session.new"]({"intent": "memory compaction"})
    session_id = created["id"]
    session = app.graph.get(UUID(session_id))
    await app.graph.add_turn(session.id, Turn(turn_id=0, role="user", content="alpha"))
    await app.graph.add_turn(session.id, Turn(turn_id=1, role="agent", content="beta"))

    compacted = await handlers["memory.compact_session"](
        {"session_id": session_id, "keep_last": 1},
    )

    assert compacted["compacted"] is True
    assert compacted["artifact"]["artifact_type"] == "capdep.compaction_summary.v1"
    entries = await handlers["memory.entries"]({})
    assert entries["entries"][0]["trust_class"] == "derived_summary"

    policy = await handlers["memory.policy"]({})
    assert policy["entry_count"] == 1
    assert policy["trust_classes"]["derived_summary"] == 1

    dry_run = await handlers["memory.prune"](
        {"trust_class": "derived_summary", "older_than_days": 0},
    )
    assert dry_run["dry_run"] is True
    assert dry_run["candidate_count"] == 1

    applied = await handlers["memory.prune"](
        {"trust_class": "derived_summary", "older_than_days": 0, "apply": True},
    )
    assert applied["deleted_count"] == 1


class _FailIfCalledLLM:
    async def respond(self, messages, tools) -> LLMResponse:  # pragma: no cover
        raise AssertionError("LLM should not be called when image generation is unavailable")


async def test_generated_image_intent_fails_closed_without_image_tool(tmp_path: Path) -> None:
    async with running_daemon(tmp_path) as running:
        running.app.llm_client = _FailIfCalledLLM()  # type: ignore[assignment]
        session = await running.client.call("session.new", {"intent": "image fail closed"})
        result = await running.client.call(
            "session.send",
            {
                "session_id": session["id"],
                "message": "generate an image of a simple landscape",
                "client_id": "test",
            },
        )

    assert result["finish_reason"] == FinishReason.STOP.value
    assert "no generated-image tool" in result["content"]
    assert "invent" in result["content"]
