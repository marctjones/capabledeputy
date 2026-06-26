#!/usr/bin/env python3
"""Minimal repro: call google-gmail MCP search_threads outside the daemon."""

from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path

import yaml
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from capabledeputy.upstream.config import UpstreamServerConfig
from capabledeputy.upstream.http_auth import httpx_auth_from_config
from capabledeputy.upstream.server_yaml import ServerYamlConfig


async def main() -> int:
    path = Path.home() / ".config/capabledeputy/servers.d/google-gmail.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    parsed = ServerYamlConfig.from_dict(raw, filename=str(path))
    config: UpstreamServerConfig = parsed.server_config
    assert config.auth is not None

    print("connecting to", config.url)
    async with streamablehttp_client(
        config.url,
        auth=httpx_auth_from_config(config.auth, server_name=config.name),
    ) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print("tools:", names[:10], "..." if len(names) > 10 else "")
            print("calling search_threads...")
            result = await session.call_tool(
                "search_threads",
                arguments={"query": "in:inbox", "pageSize": 3},
            )
            print("isError:", getattr(result, "isError", None))
            for block in result.content[:3]:
                text = getattr(block, "text", None)
                if text:
                    print(text[:2000])
            structured = getattr(result, "structuredContent", None)
            if structured:
                print("structured keys:", list(structured.keys())[:10])
    print("ok")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception:
        traceback.print_exc()
        raise SystemExit(1) from None