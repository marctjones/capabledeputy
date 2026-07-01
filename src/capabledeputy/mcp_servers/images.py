"""Legacy combined images MCP server (generate + fetch).

Prefer separate servers for independent configuration:
  - image_generate.py  → bundled-image-generate
  - image_fetch.py     → bundled-image-fetch

Run via:
  capdep mcp-server-images
  python -m capabledeputy.mcp_servers.images
"""

from __future__ import annotations

import asyncio

from capabledeputy.mcp_servers._common import ToolDescriptor, serve_tools
from capabledeputy.mcp_servers.image_fetch import tools as fetch_tools
from capabledeputy.mcp_servers.image_generate import tools as generate_tools

SERVER_NAME = "capdep-images"


def tools() -> list[ToolDescriptor]:
    return [*generate_tools(), *fetch_tools()]


async def serve() -> None:
    await serve_tools(SERVER_NAME, tools())


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()