"""Web fetch + search tools (DESIGN.md §7.4 — `untrusted.external` source).

`web.fetch` (#326): a REAL HTTP(S) fetch. The destination-gated egress floor
(#293/#296) runs at the policy chokepoint BEFORE this handler, so a confidential
session cannot exfiltrate to a non-allowlisted destination; the actuator here
only performs the (already-gated) read. It is bounded (scheme, size, timeout)
and SSRF-guarded: the target host is resolved and refused if it maps to a
loopback / private / link-local / reserved / metadata address, so a *clean*
session (which the destination floor lets fetch freely) still cannot be steered
into the local network or a cloud metadata endpoint. All output is labeled
`untrusted.external`, which propagates into the caller and constrains downstream
egress. A `WebMock` override remains for deterministic demos/tests (preloaded
content wins over the network).

Native web.search: real search tool backed by Brave Search API (if key
configured) or DuckDuckGo Instant Answer (free fallback).
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from anyio import to_thread

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

_ALLOWED_SCHEMES = ("http", "https")
_MAX_FETCH_BYTES = 5 * 1024 * 1024  # 5 MiB — bound the read
_FETCH_TIMEOUT_SECONDS = 15.0
_USER_AGENT = "CapableDeputy-web.fetch/1.0"


class WebFetchError(RuntimeError):
    """A native web.fetch failed a safety guard or the network request."""


def _ip_is_internal(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def _guard_host(host: str) -> None:
    """SSRF guard: refuse if ANY resolved address for `host` is internal. Resolve
    then check then fetch is a good-faith guard (a determined DNS-rebinding
    attacker could still race the second resolution inside urlopen — pinning the
    IP through TLS SNI is a follow-up); it closes the common metadata/loopback
    exposure that de-stubbing to a real fetch would otherwise open."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as e:
        raise WebFetchError(f"could not resolve host {host!r}: {e}") from e
    for info in infos:
        ip = str(info[4][0])
        if _ip_is_internal(ip):
            raise WebFetchError(
                f"refusing to fetch internal/private address {ip} for host {host!r}",
            )


def _fetch_url_text(url: str) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise WebFetchError(f"unsupported scheme {parsed.scheme!r}; only http(s) are allowed")
    if not parsed.hostname:
        raise WebFetchError("url has no host")
    _guard_host(parsed.hostname)
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        # scheme (http/https) + host (SSRF guard) are validated above.
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT_SECONDS) as resp:
            raw = resp.read(_MAX_FETCH_BYTES + 1)
            truncated = len(raw) > _MAX_FETCH_BYTES
            raw = raw[:_MAX_FETCH_BYTES]
            charset = resp.headers.get_content_charset() or "utf-8"
            return {
                "body": raw.decode(charset, errors="replace"),
                "status": getattr(resp, "status", None),
                "content_type": resp.headers.get_content_type(),
                "truncated": truncated,
            }
    except urllib.error.URLError as e:
        raise WebFetchError(f"fetch failed: {e}") from e


class WebMock:
    """In-memory URL→content store for demos and tests."""

    def __init__(self) -> None:
        self._content: dict[str, str] = {}

    def serve(self, url: str, content: str) -> None:
        self._content[url] = content

    def get(self, url: str) -> str | None:
        return self._content.get(url)


def make_web_tools(mock: WebMock) -> list[ToolDefinition]:
    _untrusted = LabelState(b=frozenset({ProvenanceTag(level=ProvenanceLevel.EXTERNAL_UNTRUSTED)}))

    async def web_fetch(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        url = str(args["url"])
        # Demo/test override: preloaded content wins over the network so tests
        # stay deterministic and offline.
        preloaded = mock.get(url)
        if preloaded is not None:
            return ToolResult(
                output={"found": True, "url": url, "body": preloaded, "source": "mock"},
                additional_tags=_untrusted,
            )
        # #326 — real, bounded, SSRF-guarded HTTP(S) fetch (already
        # destination-gated by the policy chokepoint before we get here).
        try:
            fetched = await to_thread.run_sync(_fetch_url_text, url)
        except WebFetchError as e:
            return ToolResult(
                output={"found": False, "url": url, "error": str(e)},
                additional_tags=_untrusted,
            )
        return ToolResult(
            output={"found": True, "url": url, "source": "network", **fetched},
            additional_tags=_untrusted,
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
