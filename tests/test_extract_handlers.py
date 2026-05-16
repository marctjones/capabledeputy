"""Tests for the REPL-driven quarantined extraction RPC."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from capabledeputy.app import App
from capabledeputy.daemon.extract_handlers import make_extract_handlers
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import FinishReason, LLMResponse
from capabledeputy.quarantined.schemas import list_schemas
from capabledeputy.tools.native.inbox import InboundMessage


@pytest.fixture
async def app(tmp_path: Path) -> App:
    fake = FakeLLMClient([])
    a = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        quarantined_llm=fake,
    )
    await a.startup()
    return a


async def test_inbox_message_extraction_returns_data(tmp_path: Path) -> None:
    """Happy-path: a quarantined LLM that returns a valid schema
    payload produces a structured result with no labels."""
    # ContactInfo: {name, relationship}
    fake = FakeLLMClient(
        [
            LLMResponse(
                content='{"name": "Alice", "relationship": "friend"}',
                finish_reason=FinishReason.STOP,
            ),
        ],
    )
    a = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        quarantined_llm=fake,
    )
    await a.startup()
    a.inbox.add(
        InboundMessage(
            id="m1",
            sender="x@y",
            subject="hi",
            body="alice is a friend",
            received_at=datetime.now(UTC),
        ),
    )

    handlers = make_extract_handlers(a)
    result = await handlers["extract.inbox_message"](
        {"message_id": "m1", "schema": "ContactInfo"},
    )
    assert result.get("error") is None, result.get("error")
    assert result["message_id"] == "m1"
    assert result["schema"] == "ContactInfo"
    assert result["data"] == {"name": "Alice", "relationship": "friend"}


async def test_inbox_message_unknown_id_returns_error(app: App) -> None:
    handlers = make_extract_handlers(app)
    result = await handlers["extract.inbox_message"](
        {"message_id": "nope", "schema": list_schemas()[0]},
    )
    assert "error" in result
    assert "no inbox message" in result["error"]


async def test_inbox_message_unknown_schema_returns_error(app: App) -> None:
    app.inbox.add(
        InboundMessage(
            id="m1",
            sender="x@y",
            subject="hi",
            body="...",
            received_at=datetime.now(UTC),
        ),
    )
    handlers = make_extract_handlers(app)
    result = await handlers["extract.inbox_message"](
        {"message_id": "m1", "schema": "no-such-schema"},
    )
    assert "error" in result
    assert "unknown schema" in result["error"]


async def test_no_quarantined_llm_returns_clear_error(tmp_path: Path) -> None:
    a = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        quarantined_llm=None,
    )
    await a.startup()
    handlers = make_extract_handlers(a)
    result = await handlers["extract.inbox_message"](
        {"message_id": "m1", "schema": "x"},
    )
    assert "error" in result
    assert "no quarantined LLM" in result["error"]


async def test_schemas_handler_lists_registered_schemas(app: App) -> None:
    handlers = make_extract_handlers(app)
    result = await handlers["extract.schemas"]({})
    assert set(result["schemas"]) == set(list_schemas())


# Suppress unused-import warning for fixture-only LLMResponse.
_ = LLMResponse
_ = FinishReason
