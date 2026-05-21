"""Standalone MCP server: web search.

Tools exposed:
  - search.web(query, count=10)
      Search the web. Backend is Brave Search by default
      (https://brave.com/search/api/); falls back to DuckDuckGo
      Instant Answer for queries that look like factoid questions.

API key:
  - BRAVE_SEARCH_API_KEY env var enables Brave (preferred)
  - With no key, falls back to DuckDuckGo (limited; instant-answer
    only — works for definitions / dates but not general queries)

Run via:
  capdep mcp-server-search
  python -m capabledeputy.mcp_servers.search
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

from capabledeputy.mcp_servers._common import ToolDescriptor, serve_tools

SERVER_NAME = "capdep-search"
BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
DDG_ENDPOINT = "https://api.duckduckgo.com/"
DEFAULT_COUNT = 10
MAX_COUNT = 20
DEFAULT_TIMEOUT = 10.0


async def _brave_search(query: str, count: int, api_key: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        resp = await client.get(
            BRAVE_ENDPOINT,
            params={"q": query, "count": min(count, MAX_COUNT)},
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


async def _ddg_search(query: str, count: int) -> dict[str, Any]:
    """DuckDuckGo Instant Answer API. Limited but free + no key.
    Returns the Abstract / RelatedTopics best-effort. For real-quality
    search results, configure BRAVE_SEARCH_API_KEY.
    """
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
    for topic in data.get("RelatedTopics", []) or []:
        if isinstance(topic, dict) and topic.get("Text"):
            results.append(
                {
                    "title": topic.get("Text", "").split(" - ", 1)[0],
                    "url": topic.get("FirstURL", ""),
                    "snippet": topic.get("Text", ""),
                },
            )
        if len(results) >= count:
            break
    return {"backend": "duckduckgo", "query": query, "count": len(results), "results": results}


async def _search_web(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args["query"]).strip()
    if not query:
        raise ValueError("query must be non-empty")
    count = int(args.get("count", DEFAULT_COUNT))
    if count < 1 or count > MAX_COUNT:
        raise ValueError(f"count must be in [1, {MAX_COUNT}]")
    api_key = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
    if api_key:
        return await _brave_search(query, count, api_key)
    return await _ddg_search(query, count)


def tools() -> list[ToolDescriptor]:
    return [
        ToolDescriptor(
            name="search.web",
            description=(
                "Search the web. Returns a list of {title, url, snippet}. "
                "Uses Brave Search if BRAVE_SEARCH_API_KEY is set; "
                "otherwise falls back to DuckDuckGo Instant Answer "
                "(limited)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "count": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_COUNT,
                        "default": DEFAULT_COUNT,
                    },
                },
                "required": ["query"],
            },
            handler=_search_web,
            annotations={"readOnlyHint": True, "openWorldHint": True, "idempotentHint": True},
        ),
    ]


async def serve() -> None:
    await serve_tools(SERVER_NAME, tools())


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
