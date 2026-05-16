"""Credential discovery for the daemon.

LiteLLM reads `ANTHROPIC_API_KEY` from the process environment. To
spare users from re-exporting it every shell session, the daemon
falls back to reading a key file (`CLAUDEAPI.KEY` by convention) from
the current working directory and populating `os.environ` before any
LLM client is constructed.

Precedence:

  1. `ANTHROPIC_API_KEY` already in the environment — wins.
  2. First existing file in `search_paths` — read, stripped of
     surrounding whitespace, and written into `os.environ`.
  3. Nothing — `os.environ` is left as-is; downstream LLM calls will
     fail with whatever error LiteLLM produces.

Missing files are not an error: a user running without an LLM (eg.
tests, dry-runs) shouldn't be blocked by this.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

ENV_VAR = "ANTHROPIC_API_KEY"
DEFAULT_KEY_FILENAME = "CLAUDEAPI.KEY"


def default_search_paths() -> list[Path]:
    return [Path.cwd() / DEFAULT_KEY_FILENAME]


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
