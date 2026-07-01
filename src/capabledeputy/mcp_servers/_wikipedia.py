"""Wikipedia summary + lead image lookup via the MediaWiki API."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

_API = "https://en.wikipedia.org/w/api.php"
_USER_AGENT = "CapableDeputy/0.x (mcp-server-fetch)"


async def wikipedia_lookup(
    title: str,
    *,
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    """Return summary text, page URL, and thumbnail for a Wikipedia title."""
    query = title.strip()
    if not query:
        return {"ok": False, "error": "title is required"}

    params = {
        "action": "query",
        "format": "json",
        "prop": "extracts|pageimages|info",
        "inprop": "url",
        "exintro": "1",
        "explaintext": "1",
        "piprop": "thumbnail|original",
        "pithumbsize": 800,
        "titles": query,
        "redirects": "1",
    }

    async with httpx.AsyncClient(
        timeout=timeout_seconds,
        follow_redirects=True,
        headers={"User-Agent": _USER_AGENT},
    ) as client:
        resp = await client.get(_API, params=params)
        if resp.status_code >= 400:
            return {
                "ok": False,
                "error": f"Wikipedia API HTTP {resp.status_code}",
                "status": resp.status_code,
            }
        payload = resp.json()

    pages = payload.get("query", {}).get("pages", {})
    if not pages:
        return {"ok": False, "error": f"no Wikipedia page found for {query!r}"}

    page = next(iter(pages.values()))
    if str(page.get("missing")) == "" or page.get("missing"):
        return {"ok": False, "error": f"Wikipedia page not found: {query!r}"}

    resolved_title = str(page.get("title") or query)
    summary = str(page.get("extract") or "").strip()
    page_url = str(page.get("fullurl") or "")
    if not page_url:
        page_url = f"https://en.wikipedia.org/wiki/{quote(resolved_title.replace(' ', '_'))}"

    thumb = page.get("thumbnail") or {}
    original = page.get("original") or {}
    image_url = str(thumb.get("source") or original.get("source") or "").strip() or None

    markdown_image = ""
    if image_url:
        markdown_image = f"![{resolved_title}]({image_url})"

    content_parts = [f"**{resolved_title}**", "", summary or "(No summary available.)"]
    if page_url:
        content_parts.extend(["", f"Source: {page_url}"])
    if markdown_image:
        content_parts.extend(["", markdown_image])

    return {
        "ok": True,
        "title": resolved_title,
        "query": query,
        "summary": summary,
        "page_url": page_url,
        "image_url": image_url,
        "markdown_image": markdown_image,
        "content": "\n".join(content_parts),
    }