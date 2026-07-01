"""Download remote images for inline CapDepMac display."""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx

_IMAGE_CONTENT_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/jpg",
        "image/gif",
        "image/webp",
        "image/tiff",
        "image/bmp",
        "image/heic",
    },
)

_EXT_BY_TYPE: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/tiff": ".tif",
    "image/bmp": ".bmp",
    "image/heic": ".heic",
}

_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_OG_IMAGE_RE_ALT = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
    re.IGNORECASE,
)


def default_image_cache_dir() -> Path:
    return Path(
        os.environ.get("CAPDEP_IMAGE_OUTPUT_DIR")
        or (Path.home() / ".capdep" / "work" / "images"),
    )


def _normalize_content_type(raw: str) -> str:
    return raw.split(";", 1)[0].strip().lower()


def _extension_for(content_type: str, url: str) -> str:
    ext = _EXT_BY_TYPE.get(_normalize_content_type(content_type))
    if ext:
        return ext
    path_ext = Path(urlparse(url).path).suffix.lower()
    if path_ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff", ".bmp", ".heic"}:
        return path_ext if path_ext != ".jpeg" else ".jpg"
    return ".jpg"


def extract_og_image_url(html: str) -> str | None:
    """Best-effort OpenGraph image URL from HTML."""
    for pattern in (_OG_IMAGE_RE, _OG_IMAGE_RE_ALT):
        match = pattern.search(html)
        if match:
            return match.group(1).strip()
    return None


async def fetch_image(
    url: str,
    *,
    alt: str | None = None,
    cache: bool = True,
    timeout_seconds: float = 15.0,
    output_dir: Path | None = None,
) -> dict[str, object]:
    """Fetch an image URL and return markdown for inline display."""
    raw = url.strip()
    if not raw:
        return {"ok": False, "error": "url is required"}

    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        return {"ok": False, "error": f"refusing non-http(s) url: {raw}"}
    if not parsed.netloc:
        return {"ok": False, "error": f"url missing host: {raw}"}

    headers = {"User-Agent": "CapableDeputy/0.x (mcp-server-image-fetch)"}
    async with httpx.AsyncClient(
        timeout=timeout_seconds,
        follow_redirects=True,
        headers=headers,
    ) as client:
        resp = await client.get(raw)
        if resp.status_code >= 400:
            return {
                "ok": False,
                "error": f"HTTP {resp.status_code} for {raw}",
                "status": resp.status_code,
            }

        content_type = _normalize_content_type(resp.headers.get("content-type", ""))
        body = resp.content
        final_url = str(resp.url)

        if content_type.startswith("text/html") or not content_type.startswith("image/"):
            og = extract_og_image_url(resp.text)
            if not og:
                return {
                    "ok": False,
                    "error": "URL is not a direct image and no og:image was found",
                    "content_type": content_type,
                    "final_url": final_url,
                }
            resp = await client.get(og)
            if resp.status_code >= 400:
                return {
                    "ok": False,
                    "error": f"HTTP {resp.status_code} for og:image {og}",
                    "status": resp.status_code,
                }
            content_type = _normalize_content_type(resp.headers.get("content-type", ""))
            body = resp.content
            final_url = str(resp.url)

    if content_type and content_type not in _IMAGE_CONTENT_TYPES and not content_type.startswith(
        "image/",
    ):
        return {
            "ok": False,
            "error": f"unsupported content-type: {content_type}",
            "final_url": final_url,
        }

    alt_text = (alt or "image").strip() or "image"
    display_url = final_url

    cached_path: str | None = None
    if cache:
        out_dir = output_dir or default_image_cache_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        ext = _extension_for(content_type, final_url)
        out_path = out_dir / f"{uuid4().hex}{ext}"
        out_path.write_bytes(body)
        cached_path = str(out_path.resolve())
        display_url = cached_path

    markdown = f"![{alt_text}]({display_url})"
    return {
        "ok": True,
        "source_url": raw,
        "final_url": final_url,
        "cached_path": cached_path,
        "content_type": content_type,
        "markdown": markdown,
        "content": f"Fetched image.\n\n{markdown}\n",
    }