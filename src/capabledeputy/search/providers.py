"""Brave + DuckDuckGo Instant Answer search backends.

Used by ``web.search`` (native) and ``bundled-search.search.web`` (MCP).
DuckDuckGo is the no-key fallback: Instant Answer API only — not general
web search. Set ``BRAVE_SEARCH_API_KEY`` for full results.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
DDG_ENDPOINT = "https://api.duckduckgo.com/"
DEFAULT_COUNT = 10
MAX_COUNT = 20
DEFAULT_TIMEOUT = 10.0

DDG_ZERO_RESULTS_HINT = (
    "DuckDuckGo Instant Answer returned no results for this query. "
    "It only answers factoid-style questions, not news or broad web search. "
    "Set BRAVE_SEARCH_API_KEY on the daemon for full search, or use the "
    "kagi_search_fetch tool when KAGI_API_KEY is configured."
)


def _clamp_count(count: int) -> int:
    return min(max(count, 1), MAX_COUNT)


async def brave_search(query: str, count: int, api_key: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        resp = await client.get(
            BRAVE_ENDPOINT,
            params={"q": query, "count": _clamp_count(count)},
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": api_key,
            },
        )
        resp.raise_for_status()
        data = resp.json()
    web = data.get("web", {}) or {}
    raw_results = web.get("results", []) or []
    results = [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("description", ""),
        }
        for r in raw_results[:count]
    ]
    return {"backend": "brave", "query": query, "count": len(results), "results": results}


def _flatten_ddg_topics(topics: list[Any]) -> list[dict[str, Any]]:
    """Flatten DDG RelatedTopics, including nested ``Topics`` groups."""
    flat: list[dict[str, Any]] = []
    for topic in topics:
        if not isinstance(topic, dict):
            continue
        nested = topic.get("Topics")
        if nested:
            flat.extend(_flatten_ddg_topics(nested))
            continue
        if topic.get("Text"):
            flat.append(topic)
    return flat


async def ddg_instant_answer_search(query: str, count: int) -> dict[str, Any]:
    """DuckDuckGo Instant Answer API — free, no key, limited coverage."""
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        resp = await client.get(
            DDG_ENDPOINT,
            params={
                "q": query,
                "format": "json",
                "no_html": "1",
                "skip_disambig": "1",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    results: list[dict[str, str]] = []
    abstract = data.get("AbstractText") or ""
    if abstract:
        results.append(
            {
                "title": data.get("Heading", query),
                "url": data.get("AbstractURL", ""),
                "snippet": abstract,
            },
        )

    for topic in _flatten_ddg_topics(data.get("RelatedTopics", []) or []):
        results.append(
            {
                "title": topic.get("Text", "").split(" - ", 1)[0],
                "url": topic.get("FirstURL", ""),
                "snippet": topic.get("Text", ""),
            },
        )
        if len(results) >= count:
            break

    payload: dict[str, Any] = {
        "backend": "duckduckgo",
        "api": "instant_answer",
        "query": query,
        "count": len(results),
        "results": results[:count],
    }
    if not results:
        payload["limitation"] = DDG_ZERO_RESULTS_HINT
    return payload


async def search_web(query: str, count: int = DEFAULT_COUNT) -> dict[str, Any]:
    """Run web search using Brave when configured, else DuckDuckGo Instant Answer."""
    normalized = query.strip()
    if not normalized:
        raise ValueError("query must be non-empty")
    clamped = _clamp_count(count)
    api_key = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
    if api_key:
        return await brave_search(normalized, clamped, api_key)
    return await ddg_instant_answer_search(normalized, clamped)