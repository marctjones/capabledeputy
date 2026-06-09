"""Select the LLM backend (opt-in via environment).

`CAPDEP_LLM_BACKEND`:
  - unset / "litellm" (default): `LiteLLMClient` → the Anthropic API, per-token,
    needs `ANTHROPIC_API_KEY`. The sanctioned path for hosted / multi-user use.
  - "claude-cli": `ClaudeCliClient` → the `claude` CLI in print mode, using YOUR
    logged-in Claude subscription. For the subscriber's OWN local use only;
    built-in tools are disabled so capdep stays the policy gate. Model via
    `CAPDEP_CLAUDE_MODEL` (a `claude` alias like "sonnet"/"opus", or a full id).
"""

from __future__ import annotations

import os

from capabledeputy.llm.client import LLMClient

_CLI_ALIASES = frozenset({"claude-cli", "claude", "cli", "subscription"})


def make_llm_client(model: str | None = None) -> LLMClient:
    backend = os.environ.get("CAPDEP_LLM_BACKEND", "litellm").strip().lower()
    if backend in _CLI_ALIASES:
        from capabledeputy.llm.claude_cli import ClaudeCliClient

        return ClaudeCliClient(model=os.environ.get("CAPDEP_CLAUDE_MODEL"))
    from capabledeputy.llm.litellm_client import LiteLLMClient

    return LiteLLMClient(model=model or os.environ.get("CAPDEP_LLM_MODEL", "claude-haiku-4-5"))
