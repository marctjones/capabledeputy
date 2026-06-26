"""Tests for shared web search providers (Brave + DuckDuckGo Instant Answer)."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from capabledeputy.search.providers import (
    _flatten_ddg_topics,
    ddg_instant_answer_search,
    search_web,
)


def test_flatten_ddg_topics_includes_nested_groups() -> None:
    topics = [
        {"Text": "Flat topic", "FirstURL": "https://example.com/a"},
        {
            "Name": "Group",
            "Topics": [
                {"Text": "Nested one", "FirstURL": "https://example.com/b"},
                {"Text": "Nested two", "FirstURL": "https://example.com/c"},
            ],
        },
    ]
    flat = _flatten_ddg_topics(topics)
    assert len(flat) == 3
    assert flat[0]["Text"] == "Flat topic"
    assert flat[1]["Text"] == "Nested one"


@pytest.mark.asyncio
async def test_ddg_instant_answer_parses_nested_topics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "AbstractText": "",
        "RelatedTopics": [
            {
                "Name": "Cats",
                "Topics": [
                    {"Text": "Cat - mammal", "FirstURL": "https://example.com/cat"},
                ],
            },
            {"Text": "Kitten - young cat", "FirstURL": "https://example.com/kitten"},
        ],
    }

    class MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            return mock_response

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: MockClient())
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)

    result = await ddg_instant_answer_search("cats", 5)

    assert result["backend"] == "duckduckgo"
    assert result["api"] == "instant_answer"
    assert result["count"] == 2
    assert result["results"][0]["title"] == "Cat"


@pytest.mark.asyncio
async def test_ddg_zero_results_includes_limitation_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "AbstractText": "",
        "RelatedTopics": [],
    }

    class MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            return mock_response

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: MockClient())

    result = await ddg_instant_answer_search("today's top headlines", 5)

    assert result["count"] == 0
    assert "limitation" in result
    assert "Instant Answer" in result["limitation"]


@pytest.mark.asyncio
async def test_search_web_uses_brave_when_key_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "test-key")

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "web": {
            "results": [
                {"title": "Headline", "url": "https://news.example", "description": "snippet"},
            ],
        },
    }

    class MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, **kwargs):
            assert "brave.com" in url
            return mock_response

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: MockClient())

    result = await search_web("headlines", 3)

    assert result["backend"] == "brave"
    assert result["count"] == 1