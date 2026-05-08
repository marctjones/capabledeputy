"""Web fetch tool stub (DESIGN.md §7.4 — `untrusted.external` source).

Demo-grade stub backed by an in-memory URL → text dictionary. Real
deployments should wrap the upstream `mcp-server-fetch` via `upstream/`
so labels apply identically; that path also gets HTTP rate limiting,
caching, and TLS verification for free. The native stub exists so the
demos can pre-load deterministic content for an URL and exercise the
label propagation without going to the network.
"""

from __future__ import annotations

from typing import Any

from capabledeputy.policy.capabilities import CapabilityKind
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

    return [
        ToolDefinition(
            name="web.fetch",
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
    ]
