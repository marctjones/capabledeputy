"""Standalone MCP server: web search.

Tools exposed:
  - search.web(query, count=10)
      Search the web. Uses Brave Search when BRAVE_SEARCH_API_KEY is set;
      otherwise DuckDuckGo Instant Answer (no key; factoid queries only).

Run via:
  capdep mcp-server-search
  python -m capabledeputy.mcp_servers.search
"""

from __future__ import annotations

import asyncio
from typing import Any

from capabledeputy.mcp_servers._common import ToolDescriptor, serve_tools
from capabledeputy.search.providers import DEFAULT_COUNT, MAX_COUNT, search_web

SERVER_NAME = "capdep-search"


async def _search_web(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args["query"]).strip()
    if not query:
        raise ValueError("query must be non-empty")
    count = int(args.get("count", DEFAULT_COUNT))
    if count < 1 or count > MAX_COUNT:
        raise ValueError(f"count must be in [1, {MAX_COUNT}]")
    result = await search_web(query, count)
    result["ok"] = True
    return result


def tools() -> list[ToolDescriptor]:
    return [
        ToolDescriptor(
            name="search.web",
            description=(
                "Search the web. Returns {title, url, snippet} results. "
                "Uses Brave Search when BRAVE_SEARCH_API_KEY is set on the "
                "daemon; otherwise DuckDuckGo Instant Answer (no API key — "
                "limited to factoid-style queries). For news/headlines, "
                "prefer kagi_search_fetch when KAGI_API_KEY is configured."
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