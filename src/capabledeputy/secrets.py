"""Credential discovery for the daemon.

LiteLLM reads `ANTHROPIC_API_KEY` from the process environment. To
spare users from re-exporting it every shell session, the daemon
falls back to reading a key file from a small ordered list of
locations and populating `os.environ` before any LLM client is
constructed.

Precedence:

  1. `ANTHROPIC_API_KEY` already in the environment — wins.
  2. First existing file in `search_paths` — read, stripped of
     surrounding whitespace, and written into `os.environ`.
  3. Nothing — `os.environ` is left as-is; downstream LLM calls will
     fail with whatever error LiteLLM produces.

Default search order:

  1. `./CLAUDEAPI.KEY` (cwd-local — original memsafe convention; lets
     a project pin its own key separately from the user-global one)
  2. `~/.config/anthropic/api.key` (user-global; canonical location
     matching the `~/.config/<service>/api.key` pattern that the
     Vast.ai and RunPod CLIs already use). This is the location
     populated when the user centralizes keys under `~/.config/`.

Missing files are not an error: a user running without an LLM (eg.
tests, dry-runs) shouldn't be blocked by this.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

ENV_VAR = "ANTHROPIC_API_KEY"
DEFAULT_KEY_FILENAME = "CLAUDEAPI.KEY"
USER_CONFIG_KEY_PATH = Path.home() / ".config" / "anthropic" / "api.key"


def default_search_paths() -> list[Path]:
    """Ordered list of key-file fallbacks. Cwd-local first so a project
    can override the user-global key when it intentionally drops a
    `CLAUDEAPI.KEY` in its own root."""
    return [Path.cwd() / DEFAULT_KEY_FILENAME, USER_CONFIG_KEY_PATH]


def load_anthropic_api_key(
    search_paths: Iterable[Path] | None = None,
) -> str | None:
    """Return the API key, also populating `os.environ[ENV_VAR]` if
    a file fallback was used. Returns None if neither the env var nor
    any candidate file produced a value.
    """
    existing = os.environ.get(ENV_VAR)
    if existing:
        return existing

    paths = list(search_paths) if search_paths is not None else default_search_paths()
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        os.environ[ENV_VAR] = text
        return text

    return None
