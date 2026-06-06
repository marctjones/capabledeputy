"""Tests for native web.search tool.

Covers:
- Tool registration with correct metadata
- Brave Search backend selection when API key is set
- DuckDuckGo fallback when no key configured
- Error handling for network failures
- Empty query rejection
- Count bounds validation
"""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import httpx
import pytest

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.labels import LabelState, ProvenanceLevel, ProvenanceTag
from capabledeputy.tools.native.web import WebMock, make_web_tools
from capabledeputy.tools.registry import ToolContext


class MockAsyncContextManager:
    """Mock async context manager for httpx.AsyncClient."""

    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False

    async def get(self, *args, **kwargs):
        return self.response


@pytest.fixture
def mock() -> WebMock:
    """Empty WebMock for web.search tests (search doesn't use it)."""
    return WebMock()


@pytest.fixture
def tool_context() -> ToolContext:
    """Tool context for test calls."""
    return ToolContext(
        session_id=uuid4(),
        label_state=LabelState(),
    )


def test_web_search_tool_registration(mock: WebMock) -> None:
    """Tool appears in registry with correct metadata."""
    tools = make_web_tools(mock)
    search_tool = next((t for t in tools if t.name == "web.search"), None)

    assert search_tool is not None
    assert search_tool.capability_kind == CapabilityKind.WEB_FETCH
    assert ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED) in search_tool.inherent_tags.b
    assert search_tool.effect_class == "data.read_remote"
    assert search_tool.default_reversibility == {"degree": "reversible", "agent": "system"}
    assert search_tool.tool_provenance == "operator-curated"
    assert search_tool.target_arg == "query"
    assert search_tool.surfaces_destination_id is False


def test_web_search_parameters_schema(mock: WebMock) -> None:
    """Parameters schema has query required, count optional with bounds."""
    tools = make_web_tools(mock)
    search_tool = next((t for t in tools if t.name == "web.search"), None)

    schema = search_tool.parameters_schema
    assert schema["type"] == "object"
    assert "query" in schema["properties"]
    assert "count" in schema["properties"]
    assert "query" in schema["required"]
    assert "count" not in schema["required"]

    count_schema = schema["properties"]["count"]
    assert count_schema["type"] == "integer"
    assert count_schema["minimum"] == 1
    assert count_schema["maximum"] == 20
    assert count_schema["default"] == 10


@pytest.mark.asyncio
async def test_empty_query_rejection(mock: WebMock, tool_context: ToolContext) -> None:
    """Empty query string returns error."""
    tools = make_web_tools(mock)
    search_tool = next((t for t in tools if t.name == "web.search"), None)

    result = await search_tool.handler({"query": ""}, tool_context)
    assert result.output["ok"] is False
    assert "non-empty" in result.output["error"]
    assert ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED) in result.additional_tags.b


@pytest.mark.asyncio
async def test_count_bounds_too_small(mock: WebMock, tool_context: ToolContext) -> None:
    """Count < 1 is rejected."""
    tools = make_web_tools(mock)
    search_tool = next((t for t in tools if t.name == "web.search"), None)

    result = await search_tool.handler({"query": "test", "count": 0}, tool_context)
    assert result.output["ok"] is False
    assert "must be in" in result.output["error"]


@pytest.mark.asyncio
async def test_count_bounds_too_large(mock: WebMock, tool_context: ToolContext) -> None:
    """Count > 20 is rejected."""
    tools = make_web_tools(mock)
    search_tool = next((t for t in tools if t.name == "web.search"), None)

    result = await search_tool.handler({"query": "test", "count": 21}, tool_context)
    assert result.output["ok"] is False
    assert "must be in" in result.output["error"]


@pytest.mark.asyncio
async def test_count_bounds_valid(
    mock: WebMock, tool_context: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Count in [1, 20] passes bounds check."""
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "AbstractText": "Test abstract",
        "Heading": "Test",
        "AbstractURL": "http://example.com",
        "RelatedTopics": [],
    }

    mock_ctx_mgr = MockAsyncContextManager(mock_response)

    def mock_client_class(*args, **kwargs):
        return mock_ctx_mgr

    monkeypatch.setattr(httpx, "AsyncClient", mock_client_class)

    tools = make_web_tools(mock)
    search_tool = next((t for t in tools if t.name == "web.search"), None)

    result = await search_tool.handler({"query": "test", "count": 5}, tool_context)
    assert result.output["ok"] is True


@pytest.mark.asyncio
async def test_brave_backend_selection(
    mock: WebMock,
    tool_context: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When BRAVE_SEARCH_API_KEY is set, Brave backend is used."""
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "test-key-123")

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "web": {
            "results": [
                {
                    "title": "Example",
                    "url": "http://example.com",
                    "description": "Example search result",
                },
            ],
        },
    }

    class TrackingMockClient(MockAsyncContextManager):
        def __init__(self, response):
            super().__init__(response)
            self.call_args = None

        async def get(self, *args, **kwargs):
            self.call_args = (args, kwargs)
            return self.response

    mock_client_instance = TrackingMockClient(mock_response)

    def mock_client_class(*args, **kwargs):
        return mock_client_instance

    monkeypatch.setattr(httpx, "AsyncClient", mock_client_class)

    tools = make_web_tools(mock)
    search_tool = next((t for t in tools if t.name == "web.search"), None)

    result = await search_tool.handler({"query": "test"}, tool_context)

    assert result.output["ok"] is True
    assert result.output["backend"] == "brave"
    assert mock_client_instance.call_args is not None
    args, kwargs = mock_client_instance.call_args
    assert "api.search.brave.com" in args[0]
    assert kwargs["headers"]["X-Subscription-Token"] == "test-key-123"


@pytest.mark.asyncio
async def test_ddg_fallback_no_key(
    mock: WebMock,
    tool_context: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no API key is set, DDG backend is used."""
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "AbstractText": "Test definition",
        "Heading": "TestQuery",
        "AbstractURL": "http://example.com",
        "RelatedTopics": [],
    }

    class TrackingMockClient(MockAsyncContextManager):
        def __init__(self, response):
            super().__init__(response)
            self.call_args = None

        async def get(self, *args, **kwargs):
            self.call_args = (args, kwargs)
            return self.response

    mock_client_instance = TrackingMockClient(mock_response)

    def mock_client_class(*args, **kwargs):
        return mock_client_instance

    monkeypatch.setattr(httpx, "AsyncClient", mock_client_class)

    tools = make_web_tools(mock)
    search_tool = next((t for t in tools if t.name == "web.search"), None)

    result = await search_tool.handler({"query": "test"}, tool_context)

    assert result.output["ok"] is True
    assert result.output["backend"] == "duckduckgo"
    assert mock_client_instance.call_args is not None
    args, kwargs = mock_client_instance.call_args
    assert "duckduckgo.com" in args[0]


@pytest.mark.asyncio
async def test_network_error_handling(
    mock: WebMock,
    tool_context: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Network failures return error result, don't crash."""
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)

    class ErrorMockClient(MockAsyncContextManager):
        async def get(self, *args, **kwargs):
            raise httpx.ConnectError("connection refused")

    mock_client_instance = ErrorMockClient(None)

    def mock_client_class(*args, **kwargs):
        return mock_client_instance

    monkeypatch.setattr(httpx, "AsyncClient", mock_client_class)

    tools = make_web_tools(mock)
    search_tool = next((t for t in tools if t.name == "web.search"), None)

    result = await search_tool.handler({"query": "test"}, tool_context)

    assert result.output["ok"] is False
    assert "search failed" in result.output["error"]
    assert ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED) in result.additional_tags.b


@pytest.mark.asyncio
async def test_search_result_format(
    mock: WebMock,
    tool_context: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Search results include required fields: title, url, snippet."""
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "AbstractText": "A great result",
        "Heading": "My Query",
        "AbstractURL": "http://result.com",
        "RelatedTopics": [
            {
                "Text": "Related - more info",
                "FirstURL": "http://related.com",
            },
        ],
    }

    mock_ctx_mgr = MockAsyncContextManager(mock_response)

    def mock_client_class(*args, **kwargs):
        return mock_ctx_mgr

    monkeypatch.setattr(httpx, "AsyncClient", mock_client_class)

    tools = make_web_tools(mock)
    search_tool = next((t for t in tools if t.name == "web.search"), None)

    result = await search_tool.handler({"query": "test", "count": 10}, tool_context)

    assert result.output["ok"] is True
    assert "results" in result.output
    assert isinstance(result.output["results"], list)
    assert len(result.output["results"]) > 0
    for item in result.output["results"]:
        assert "title" in item
        assert "url" in item
        assert "snippet" in item


@pytest.mark.asyncio
async def test_count_respected(
    mock: WebMock,
    tool_context: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returned result count respects the count parameter."""
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "AbstractText": "First",
        "Heading": "Query",
        "AbstractURL": "http://1.com",
        "RelatedTopics": [
            {"Text": "Second - more", "FirstURL": "http://2.com"},
            {"Text": "Third - extra", "FirstURL": "http://3.com"},
            {"Text": "Fourth - more", "FirstURL": "http://4.com"},
        ],
    }

    mock_ctx_mgr = MockAsyncContextManager(mock_response)

    def mock_client_class(*args, **kwargs):
        return mock_ctx_mgr

    monkeypatch.setattr(httpx, "AsyncClient", mock_client_class)

    tools = make_web_tools(mock)
    search_tool = next((t for t in tools if t.name == "web.search"), None)

    result = await search_tool.handler({"query": "test", "count": 2}, tool_context)

    assert result.output["ok"] is True
    assert result.output["count"] <= 2
    assert len(result.output["results"]) <= 2


@pytest.mark.asyncio
async def test_untrusted_external_label_always_applied(
    mock: WebMock,
    tool_context: ToolContext,
) -> None:
    """UNTRUSTED_EXTERNAL label is applied to all search result outcomes."""
    # Test on error
    tools = make_web_tools(mock)
    search_tool = next((t for t in tools if t.name == "web.search"), None)

    result = await search_tool.handler({"query": ""}, tool_context)
    assert ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED) in result.additional_tags.b

    result = await search_tool.handler({"query": "test", "count": 100}, tool_context)
    assert ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED) in result.additional_tags.b


def test_web_fetch_tool_still_works(mock: WebMock) -> None:
    """Verify web.fetch tool is not broken by web.search addition."""
    tools = make_web_tools(mock)
    fetch_tool = next((t for t in tools if t.name == "web.fetch"), None)

    assert fetch_tool is not None
    assert fetch_tool.capability_kind == CapabilityKind.WEB_FETCH
    assert ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED) in fetch_tool.inherent_tags.b
    assert fetch_tool.target_arg == "url"
