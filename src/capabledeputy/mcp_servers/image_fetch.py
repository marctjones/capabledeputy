"""Bundled MCP server: remote image fetch for inline CapDepMac display.

Lightweight — uses httpx only; runs in the main CapDep venv (no torch).

Run via:
  capdep mcp-server-image-fetch
  python -m capabledeputy.mcp_servers.image_fetch
"""

from __future__ import annotations

import asyncio
from typing import Any

from capabledeputy.mcp_servers._common import ToolDescriptor, serve_tools
from capabledeputy.mcp_servers._image_fetch import fetch_image

SERVER_NAME = "capdep-image-fetch"


async def _fetch(args: dict[str, Any]) -> dict[str, Any]:
    url = str(args.get("url") or "").strip()
    if not url:
        raise ValueError("url is required")
    cache_raw = args.get("cache")
    cache = True if cache_raw is None else bool(cache_raw)
    timeout_raw = args.get("timeout_seconds")
    timeout = float(timeout_raw) if timeout_raw is not None else 15.0
    return await fetch_image(
        url,
        alt=str(args.get("alt") or "").strip() or None,
        cache=cache,
        timeout_seconds=timeout,
    )


def tools() -> list[ToolDescriptor]:
    return [
        ToolDescriptor(
            name="image.fetch",
            description=(
                "Fetch a remote image URL (or extract og:image from an HTML page) "
                "and return markdown for inline CapDepMac display. By default caches "
                "to ~/.capdep/work/images/; set cache=false to emit the https URL.\n\n"
                "USE THIS WHEN: you have a direct image URL or page URL from "
                "wikipedia.lookup, search.web, or the user. Include the returned "
                "`markdown` in your reply."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Direct image URL or page URL with og:image.",
                    },
                    "alt": {
                        "type": "string",
                        "description": "Alt text for inline markdown image.",
                    },
                    "cache": {
                        "type": "boolean",
                        "description": "Save locally for display (default true).",
                    },
                    "timeout_seconds": {
                        "type": "number",
                        "description": "HTTP timeout (default 15).",
                    },
                },
                "required": ["url"],
            },
            handler=_fetch,
            annotations={"readOnlyHint": True, "openWorldHint": True, "idempotentHint": True},
        ),
    ]


async def serve() -> None:
    await serve_tools(SERVER_NAME, tools())


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
