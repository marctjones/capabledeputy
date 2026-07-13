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

from typing import Any

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.policy.labels import (
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
)
from capabledeputy.search.providers import (
    DEFAULT_COUNT as _DEFAULT_SEARCH_COUNT,
)
from capabledeputy.search.providers import (
    MAX_COUNT as _MAX_SEARCH_COUNT,
)
from capabledeputy.search.providers import (
    search_web as _search_web_provider,
)
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolResult


class WebMock:
    """In-memory URL→content store for demos and tests."""

    def __init__(self) -> None:
        self._content: dict[str, str] = {}

    def serve(self, url: str, content: str) -> None:
        self._content[url] = content

    def get(self, url: str) -> str | None:
        return self._content.get(url)


def make_web_tools(mock: WebMock) -> list[ToolDefinition]:
    async def web_fetch(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        url = str(args["url"])
        body = mock.get(url)
        if body is None:
            return ToolResult(
                output={"found": False, "url": url},
                additional_tags=LabelState(
                    b=frozenset({ProvenanceTag(level=ProvenanceLevel.EXTERNAL_UNTRUSTED)})
                ),
            )
        return ToolResult(
            output={"found": True, "url": url, "body": body},
            additional_tags=LabelState(
                b=frozenset({ProvenanceTag(level=ProvenanceLevel.EXTERNAL_UNTRUSTED)})
            ),
        )

    async def web_search(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        query = str(args["query"]).strip()
        if not query:
            return ToolResult(
                output={"ok": False, "error": "query must be non-empty"},
                additional_tags=LabelState(
                    b=frozenset({ProvenanceTag(level=ProvenanceLevel.EXTERNAL_UNTRUSTED)})
                ),
            )
        count = int(args.get("count", _DEFAULT_SEARCH_COUNT))
        if count < 1 or count > _MAX_SEARCH_COUNT:
            return ToolResult(
                output={
                    "ok": False,
                    "error": f"count must be in [1, {_MAX_SEARCH_COUNT}]",
                },
                additional_tags=LabelState(
                    b=frozenset({ProvenanceTag(level=ProvenanceLevel.EXTERNAL_UNTRUSTED)})
                ),
            )
        try:
            result = await _search_web_provider(query, count)
            result["ok"] = True
            return ToolResult(
                output=result,
                additional_tags=LabelState(
                    b=frozenset({ProvenanceTag(level=ProvenanceLevel.EXTERNAL_UNTRUSTED)})
                ),
            )
        except Exception as e:
            return ToolResult(
                output={"ok": False, "error": f"search failed: {e!s}"},
                additional_tags=LabelState(
                    b=frozenset({ProvenanceTag(level=ProvenanceLevel.EXTERNAL_UNTRUSTED)})
                ),
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
            inherent_tags=LabelState(
                b=frozenset({ProvenanceTag(level=ProvenanceLevel.EXTERNAL_UNTRUSTED)})
            ),
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
                "{title, url, snippet}. Uses Brave Search when "
                "BRAVE_SEARCH_API_KEY is set; otherwise DuckDuckGo "
                "Instant Answer (no key, factoid queries only). For "
                "news/headlines prefer kagi_search_fetch when "
                "KAGI_API_KEY is configured. Results are labeled "
                "untrusted.external. Required: query. Optional: count "
                "(1-20, default 10)."
            ),
            capability_kind=CapabilityKind.WEB_FETCH,
            handler=web_search,
            target_arg="query",
            inherent_tags=LabelState(
                b=frozenset({ProvenanceTag(level=ProvenanceLevel.EXTERNAL_UNTRUSTED)})
            ),
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
