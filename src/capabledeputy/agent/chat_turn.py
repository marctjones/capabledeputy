"""Heuristics for lightweight conversational turns (no tool surface)."""

from __future__ import annotations

import re

# Cap completion length on fast chat turns so MLX returns sooner on M-series
# chips without truncating normal assistant answers.
CHAT_MAX_TOKENS = 512

_WEB_SEARCH_INTENT_RE = re.compile(
    r"\b(websearch|headlines?|news|internet|online|website|wikipedia|kagi|look up|lookup)\b|"
    r"\bsearch\b.{0,40}\b(web|internet|online|headlines?|news)\b|"
    r"\b(web|internet|online)\b.{0,40}\bsearch\b",
    re.IGNORECASE,
)

_TOOL_INTENT_RE = re.compile(
    r"\b("
    r"email|inbox|mail|gmail|calendar|event|meeting|schedule|"
    r"file|folder|document|drive|read|write|search|websearch|fetch|browse|"
    r"headlines?|news|internet|online|website|wikipedia|kagi|"
    r"git|repo|commit|branch|memory|note|draft|send|delete|"
    r"approve|grant|tool|workflow|summar(?:y|ize)|triage|"
    r"download|upload|export|import|open|list|find|look up|lookup"
    r")\b",
    re.IGNORECASE,
)


def has_web_search_intent(message: str) -> bool:
    """True when the user message is asking for web/news search."""
    stripped = message.strip()
    return bool(stripped and _WEB_SEARCH_INTENT_RE.search(stripped))


def is_conversational_turn(message: str) -> bool:
    """True when the user message is likely pure chat — no tool calls needed.

    Short greetings, small talk, and general knowledge questions should
    route to the fast planner with an empty tool surface so MLX is not
    fed a 15k-token tool catalog on every turn.
    """
    stripped = message.strip()
    if not stripped:
        return False
    if len(stripped) > 320:
        return False
    if _TOOL_INTENT_RE.search(stripped):
        return False
    return True