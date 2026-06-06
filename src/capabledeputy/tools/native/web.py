"""Web fetch tool stub (DESIGN.md §7.4 — `untrusted.external` source).

Demo-grade stub backed by an in-memory URL → text dictionary. Real
deployments should wrap the upstream `mcp-server-fetch` via `upstream/`
so labels apply identically; that path also gets HTTP rate limiting,
caching, and TLS verification for free. The native stub exists so the
demos can pre-load deterministic content for an URL and exercise the
label propagation without going to the network.

Native web.search: real search tool backed by Brave Search API (if key
configured) or DuckDuckGo Instant Answer (free fallback).
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.policy.labels import Label
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolResult


class WebMock:
    """In-memory URL→content store for demos and tests."""

    def __init__(self) -> None:
        self._content: dict[str, str] = {}

    def serve(self, url: str, content: str) -> None:
        self._content[url] = content

    def get(self, url: str) -> str | None:
        return self._content.get(url)


_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_DDG_ENDPOINT = "https://api.duckduckgo.com/"
_DEFAULT_SEARCH_COUNT = 10
_MAX_SEARCH_COUNT = 20
_DEFAULT_TIMEOUT = 10.0


async def _brave_search(query: str, count: int, api_key: str) -> dict[str, Any]:
    """Call Brave Search API."""
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
        resp = await client.get(
            _BRAVE_ENDPOINT,
            params={"q": query, "count": min(count, _MAX_SEARCH_COUNT)},
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
    """DuckDuckGo Instant Answer API. Limited but free + no key."""
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
        resp = await client.get(
            _DDG_ENDPOINT,
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


def make_web_tools(mock: WebMock) -> list[ToolDefinition]:
    async def web_fetch(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        url = str(args["url"])
        body = mock.get(url)
        if body is None:
            return ToolResult(
                output={"found": False, "url": url},
                additional_labels=frozenset({Label.UNTRUSTED_EXTERNAL}),
            )
        return ToolResult(
            output={"found": True, "url": url, "body": body},
            additional_labels=frozenset({Label.UNTRUSTED_EXTERNAL}),
        )

    async def web_search(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        query = str(args["query"]).strip()
        if not query:
            return ToolResult(
                output={"ok": False, "error": "query must be non-empty"},
                additional_labels=frozenset({Label.UNTRUSTED_EXTERNAL}),
            )
        count = int(args.get("count", _DEFAULT_SEARCH_COUNT))
        if count < 1 or count > _MAX_SEARCH_COUNT:
            return ToolResult(
                output={
                    "ok": False,
                    "error": f"count must be in [1, {_MAX_SEARCH_COUNT}]",
                },
                additional_labels=frozenset({Label.UNTRUSTED_EXTERNAL}),
            )
        api_key = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
        try:
            if api_key:
                result = await _brave_search(query, count, api_key)
            else:
                result = await _ddg_search(query, count)
            result["ok"] = True
            return ToolResult(
                output=result,
                additional_labels=frozenset({Label.UNTRUSTED_EXTERNAL}),
            )
        except Exception as e:
            return ToolResult(
                output={"ok": False, "error": f"search failed: {e!s}"},
                additional_labels=frozenset({Label.UNTRUSTED_EXTERNAL}),
            )

    return [
        ToolDefinition(
            name="web.fetch",
            operations=(Operation(EffectClass.FETCH, subtype="web.fetch"),),
            risk_ids=("RISK-INDIRECT-INJECTION",),
            effect_class="data.read_remote",
            default_reversibility={"degree": "reversible", "agent": "system"},
            tool_provenance="operator-curated",
            surfaces_destination_id=True,
            description=(
                "Fetch text content from a URL. ALL responses are labeled "
                "untrusted.external; this label propagates into the calling "
                "session and constrains downstream egress per the policy "
                "engine's untrusted-meets-egress rule. Required args: url "
                "(string)."
            ),
            capability_kind=CapabilityKind.WEB_FETCH,
            handler=web_fetch,
            target_arg="url",
            inherent_labels=frozenset({Label.UNTRUSTED_EXTERNAL}),
            parameters_schema={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        ),
        ToolDefinition(
            name="web.search",
            operations=(Operation(EffectClass.FETCH, subtype="web.search"),),
            risk_ids=("RISK-INDIRECT-INJECTION",),
            effect_class="data.read_remote",
            default_reversibility={"degree": "reversible", "agent": "system"},
            tool_provenance="operator-curated",
            surfaces_destination_id=False,
            description=(
                "Search the web using text query. Returns a list of "
                "{title, url, snippet}. Uses Brave Search if "
                "BRAVE_SEARCH_API_KEY is set; otherwise falls back to "
                "DuckDuckGo Instant Answer (limited). Results are labeled "
                "untrusted.external and constrain downstream egress. "
                "Required args: query (string). Optional args: count "
                "(integer, 1-20, default 10)."
            ),
            capability_kind=CapabilityKind.WEB_FETCH,
            handler=web_search,
            target_arg="query",
            inherent_labels=frozenset({Label.UNTRUSTED_EXTERNAL}),
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "count": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": _MAX_SEARCH_COUNT,
                        "default": _DEFAULT_SEARCH_COUNT,
                    },
                },
                "required": ["query"],
            },
        ),
    ]
