"""Standalone MCP server: HTTP fetch and Wikipedia lookup.

Tools exposed:
  - fetch.get(url, timeout_seconds=10)
      GET an HTTP/HTTPS URL; returns status, headers (subset),
      content-type, body (text, truncated to 256KB).
      Refuses non-http(s) schemes.
  - wikipedia.lookup(title)
      Wikipedia summary, page URL, and lead image URL via MediaWiki API.

Designed for use behind CapableDeputy's chokepoint: every response
is labeled untrusted.external by the binding+adapter layer, so the
session that fetched is automatically tainted.

Run via:
  capdep mcp-server-fetch
  python -m capabledeputy.mcp_servers.fetch
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlparse

import httpx

from capabledeputy.mcp_servers._common import ToolDescriptor, serve_tools
from capabledeputy.mcp_servers._wikipedia import wikipedia_lookup

SERVER_NAME = "capdep-fetch"
DEFAULT_TIMEOUT = 10.0
MAX_BODY_BYTES = 256 * 1024


async def _fetch_get(args: dict[str, Any]) -> dict[str, Any]:
    url = str(args["url"])
    timeout = float(args.get("timeout_seconds", DEFAULT_TIMEOUT))
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"refusing non-http(s) scheme: {parsed.scheme}")
    if not parsed.netloc:
        raise ValueError(f"url missing host: {url}")

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "CapableDeputy/0.x (mcp-server-fetch)"},
    ) as client:
        resp = await client.get(url)
    body = resp.text
    truncated = False
    encoded = body.encode("utf-8")
    if len(encoded) > MAX_BODY_BYTES:
        # Truncate at byte boundary, drop any trailing partial char.
        truncated_bytes = encoded[:MAX_BODY_BYTES]
        body = truncated_bytes.decode("utf-8", errors="ignore")
        truncated = True
    # Subset of headers worth surfacing; full headers can be huge.
    selected_headers = {
        k: v
        for k, v in resp.headers.items()
        if k.lower() in {"content-type", "content-length", "last-modified", "etag"}
    }
    return {
        "url": str(resp.url),
        "status": resp.status_code,
        "content_type": resp.headers.get("content-type", ""),
        "headers": selected_headers,
        "body": body,
        "truncated": truncated,
        "body_size": len(encoded),
    }


async def _wikipedia_lookup(args: dict[str, Any]) -> dict[str, Any]:
    title = str(args.get("title") or "").strip()
    if not title:
        raise ValueError("title is required")
    timeout_raw = args.get("timeout_seconds")
    timeout = float(timeout_raw) if timeout_raw is not None else 15.0
    return await wikipedia_lookup(title, timeout_seconds=timeout)


def tools() -> list[ToolDescriptor]:
    return [
        ToolDescriptor(
            name="fetch.get",
            description=(
                "HTTP GET a URL; returns status, content-type, body "
                f"(truncated at {MAX_BODY_BYTES} bytes). Refuses non-"
                "http(s) schemes. Use timeout_seconds (default "
                f"{int(DEFAULT_TIMEOUT)}) to bound the request."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "timeout_seconds": {"type": "number"},
                },
                "required": ["url"],
            },
            handler=_fetch_get,
            annotations={"readOnlyHint": True, "openWorldHint": True, "idempotentHint": True},
        ),
        ToolDescriptor(
            name="wikipedia.lookup",
            description=(
                "Look up a Wikipedia article by title. Returns summary text, "
                "page_url, image_url (lead thumbnail when available), and "
                "markdown_image for inline display.\n\n"
                "USE THIS WHEN: the user asks for information or a photo from "
                "Wikipedia. For inline images, call this then bundled-image-fetch.image.fetch "
                "with image_url (or include markdown_image when cache=false fetch is "
                "not needed — https URLs render inline in CapDepMac)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Wikipedia article title or subject name.",
                    },
                    "timeout_seconds": {
                        "type": "number",
                        "description": "HTTP timeout (default 15).",
                    },
                },
                "required": ["title"],
            },
            handler=_wikipedia_lookup,
            annotations={"readOnlyHint": True, "openWorldHint": True, "idempotentHint": True},
        ),
    ]


async def serve() -> None:
    await serve_tools(SERVER_NAME, tools())


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
