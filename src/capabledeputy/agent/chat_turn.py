"""Heuristics for lightweight conversational turns (no tool surface)."""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from capabledeputy.tools.client import ToolCallOutcome

# Cap completion length on fast chat turns so MLX returns sooner on M-series
# chips without truncating normal assistant answers.
CHAT_MAX_TOKENS = 512

_WEB_SEARCH_INTENT_RE = re.compile(
    r"\b(websearch|headlines?|news|internet|online|website|wikipedia|kagi|look up|lookup)\b|"
    r"\bsearch\b.{0,40}\b(web|internet|online|headlines?|news)\b|"
    r"\b(web|internet|online)\b.{0,40}\bsearch\b",
    re.IGNORECASE,
)

_WIKIPEDIA_LOOKUP_INTENT_RE = re.compile(
    r"\bwikipedia\b|"
    r"\b(?:information|info)\s+(?:on|about)\b|"
    r"\b(?:tell|show)\s+me\s+(?:information|info|details)\s+(?:on|about)\b|"
    r"\bwho\s+is\b",
    re.IGNORECASE,
)

_TOOL_INTENT_RE = re.compile(
    r"\b("
    r"email|inbox|mail|gmail|calendar|event|meeting|schedule|"
    r"file|folder|document|drive|read|write|search|websearch|fetch|browse|"
    r"headlines?|news|internet|online|website|wikipedia|kagi|research|"
    r"git|repo|commit|branch|memory|note|draft|send|delete|"
    r"approve|grant|tool|workflow|summar(?:y|ize)|triage|"
    r"download|upload|export|import|open|list|find|look up|lookup|"
    r"image|images|picture|pictures|photo|photos|screenshot|plot|chart|"
    r"graph|graphs|generate|visuali[sz]e|diagram|draw|show|render|inline|demo|"
    r"cartoon|photorealistic|photoreal|realistic|graphic|anime|illustration|"
    r"instead|dog|cat|woman|women|man|men|portrait"
    r")\b",
    re.IGNORECASE,
)

_CHART_GENERATION_INTENT_RE = re.compile(
    r"\b("
    r"line\s+graph|bar\s+chart|chart|graph|plot|visuali[sz]e|diagram|"
    r"generate.{0,24}(chart|graph|plot)|"
    r"(chart|graph|plot).{0,24}generat|"
    r"show.{0,32}(chart|graph|plot|inline)"
    r")\b",
    re.IGNORECASE,
)

_IMAGE_SCENE_RE = re.compile(
    r"\b("
    r"portrait|photo|picture|scene|character|woman|women|man|men|person|"
    r"illustration|artwork|nude|naked|nsfw|erotic|porn|sexual|explicit|"
    r"blonde|brunette|model|fantasy|comic|graphic\s+novel|dog|cat|"
    r"cock|dick|penis|breasts|tits|boobs|ass|butt|pussy|vagina|"
    r"bird|animal|horse|rooster"
    r")\b",
    re.IGNORECASE,
)

_IMAGE_SHOW_DISPLAY_RE = re.compile(
    r"\b(?:show|display)\s+(?:me\s+)?(?:a\s+|an\s+|the\s+)?",
    re.IGNORECASE,
)

_IMAGE_INFO_QUERY_RE = re.compile(
    r"\b(?:information|info|details|article|summary|how\s+to|why|what\s+is|"
    r"who\s+is|weather|email|inbox|calendar|schedule|meeting)\b",
    re.IGNORECASE,
)

_IMAGE_FETCH_INTENT_RE = re.compile(
    r"\b(from|on)\s+(wikipedia|the\s+web|a\s+website|another\s+website|"
    r"the\s+internet|wikimedia)\b|"
    r"\bwikipedia\b.{0,64}\b(image|images|picture|pictures|photo|photos)\b|"
    r"\b(image|images|picture|pictures|photo|photos)\b.{0,64}\b(wikipedia|website|web)\b|"
    r"\bfetch\b.{0,32}\b(image|picture|photo)\b",
    re.IGNORECASE,
)

_IMAGE_GENERATION_INTENT_RE = re.compile(
    r"\b("
    r"generate|create|make|draw|render|paint|show|produce"
    r")\b.{0,48}\b("
    r"image|images|picture|pictures|photo|photos|portrait|portraits|"
    r"illustration|illustrations|artwork|scene|scenes"
    r")\b|"
    r"\b(image|picture|photo|portrait|illustration|artwork|scene)\b"
    r".{0,48}\b(generate|create|make|draw|render|paint)\b|"
    r"\b(nude|naked|nsfw|erotic|pornographic|sexual|explicit)\b|"
    r"\b(attractive|beautiful)\b.{0,24}\b(woman|women|man|men|person)\b",
    re.IGNORECASE,
)

_IMAGE_STYLE_FOLLOWUP_RE = re.compile(
    r"^(?:"
    r"cartoon|photorealistic|photoreal|realistic|graphic(?:\s*novel)?|anime|"
    r"illustration|graphic_novel|instead|a\s+dog|a\s+cat|dog|cat"
    r")$",
    re.IGNORECASE,
)

_IMAGE_REFINEMENT_RE = re.compile(
    r"\b(instead|rather|switch|change)\b.{0,32}\b(dog|cat|woman|man|style|cartoon|"
    r"photoreal|realistic|graphic)\b|"
    r"\b(do|make|show)\b.{0,16}\b(dog|cat)\b",
    re.IGNORECASE,
)

_IMAGE_GENERATION_REFUSAL_RE = re.compile(
    r"\b("
    r"cannot|can't|unable|won't|will not|not able|not comfortable|"
    r"refuse|decline|inappropriate|non-consensual|policy|safety|aligned"
    r")\b",
    re.IGNORECASE,
)

_IMAGE_MARKDOWN_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")

IMAGE_GENERATION_RETRY_NOTICE = (
    "MANDATORY: the user requested a generated image. You MUST call "
    "`bundled-image-generate.image.generate` (or `image.generate`) now with a "
    "descriptive fictional `prompt` — generic adult portraits of women or "
    "men are allowed. Do NOT refuse in prose. Do NOT claim you cannot "
    "generate images. The image backend is operator configured. Respond with ONLY a "
    '{"tool_calls": [{"id": "1", "name": "bundled-image-generate.image.generate", '
    '"args": {"prompt": "...", "style": "photoreal"}}]} JSON object.'
)

IMAGE_PATH_HALLUCINATION_RETRY_NOTICE = (
    "STOP: you pasted markdown for a generated image without calling "
    "`bundled-image-generate.image.generate` (or you reused a path from an earlier "
    "turn). Do NOT echo prior `~/.capdep/work/images/` paths. Call "
    "`bundled-image-generate.image.generate` now, wait for the tool result, then "
    "paste ONLY the `markdown` field returned by the tool."
)


def has_chart_generation_intent(message: str) -> bool:
    """True when the user wants a generated chart/graph shown inline."""
    stripped = message.strip()
    return bool(stripped and _CHART_GENERATION_INTENT_RE.search(stripped))


def has_wikipedia_lookup_intent(message: str) -> bool:
    """True when the user wants Wikipedia summary and/or lead image."""
    stripped = message.strip()
    return bool(stripped and _WIKIPEDIA_LOOKUP_INTENT_RE.search(stripped))


def has_image_fetch_intent(message: str) -> bool:
    """True when the user wants a remote/web image, not local generation."""
    stripped = message.strip()
    return bool(stripped and _IMAGE_FETCH_INTENT_RE.search(stripped))


def has_image_generation_intent(message: str) -> bool:
    """True when the user wants a generated image (not a data chart or web fetch)."""
    stripped = message.strip()
    if not stripped:
        return False
    if has_image_fetch_intent(stripped):
        return False
    if has_wikipedia_lookup_intent(stripped) and not _IMAGE_SCENE_RE.search(stripped):
        return False
    if has_chart_generation_intent(stripped):
        return bool(_IMAGE_SCENE_RE.search(stripped))
    if _IMAGE_STYLE_FOLLOWUP_RE.match(stripped) or _IMAGE_REFINEMENT_RE.search(stripped):
        return True
    if _IMAGE_GENERATION_INTENT_RE.search(stripped):
        return True
    return bool(
        _IMAGE_SHOW_DISPLAY_RE.search(stripped)
        and _IMAGE_SCENE_RE.search(stripped)
        and not _IMAGE_INFO_QUERY_RE.search(stripped)
    )


def has_probable_image_generation_intent(message: str) -> bool:
    """Broader image intent used when the model refuses in prose instead of calling tools."""
    stripped = message.strip()
    if not stripped:
        return False
    if has_image_generation_intent(stripped):
        return True
    if has_image_fetch_intent(stripped) or has_chart_generation_intent(stripped):
        return False
    if _IMAGE_INFO_QUERY_RE.search(stripped):
        return False
    return bool(
        re.search(
            r"\b(?:show|display|draw|paint|render|generate|create|make)\b",
            stripped,
            re.IGNORECASE,
        )
        and _IMAGE_SCENE_RE.search(stripped)
    )


def should_force_image_generate_tool(message: str) -> bool:
    """True when the planner must call image.generate on the first LLM pass."""
    stripped = message.strip()
    if not stripped:
        return False
    return has_image_generation_intent(stripped)


def extract_markdown_image_paths(content: str) -> tuple[str, ...]:
    return tuple(match.group(1).strip() for match in _IMAGE_MARKDOWN_RE.finditer(content))


def normalize_image_path(path: str) -> str:
    return os.path.expanduser(path.strip())


def is_generated_work_image_path(path: str) -> bool:
    expanded = normalize_image_path(path).replace("\\", "/")
    if expanded.startswith(("http://", "https://")):
        return False
    return "/.capdep/work/images/" in expanded


def _is_image_generate_tool_name(name: str | None) -> bool:
    return bool(name and name in _image_generate_tool_names())


def image_paths_from_tool_outcome(outcome: ToolCallOutcome) -> frozenset[str]:
    from capabledeputy.mcp_server.media_results import iter_image_sources_in_value
    from capabledeputy.policy.rules import Decision

    if not _is_image_generate_tool_name(outcome.tool_name):
        return frozenset()
    if outcome.decision != Decision.ALLOW:
        return frozenset()
    paths: set[str] = set()
    for source, _alt in iter_image_sources_in_value(outcome.output or {}):
        if is_generated_work_image_path(source):
            paths.add(normalize_image_path(source))
    return frozenset(paths)


def allowed_image_generate_paths(outcomes: list[Any]) -> frozenset[str]:
    allowed: set[str] = set()
    for outcome in outcomes:
        allowed |= set(image_paths_from_tool_outcome(outcome))
    return frozenset(allowed)


def collect_prior_work_image_paths(*texts: str | None) -> frozenset[str]:
    paths: set[str] = set()
    for text in texts:
        if not text:
            continue
        for raw in extract_markdown_image_paths(text):
            if is_generated_work_image_path(raw):
                paths.add(normalize_image_path(raw))
    return frozenset(paths)


def looks_like_hallucinated_image_markdown(
    content: str,
    *,
    prior_paths: frozenset[str],
    allowed_paths: frozenset[str],
) -> bool:
    """True when markdown cites work/images paths without a matching tool result."""
    cited = [
        normalize_image_path(path)
        for path in extract_markdown_image_paths(content)
        if is_generated_work_image_path(path)
    ]
    if not cited:
        return False
    if not allowed_paths:
        return True
    allowed_norm = {normalize_image_path(path) for path in allowed_paths}
    if any(path not in allowed_norm for path in cited):
        return True
    return bool(set(cited).issubset(prior_paths) and cited and allowed_norm - set(cited))


def preferred_image_markdown_from_outcomes(outcomes: list[Any]) -> str | None:
    from capabledeputy.policy.rules import Decision

    for outcome in outcomes:
        if not _is_image_generate_tool_name(outcome.tool_name):
            continue
        if outcome.decision != Decision.ALLOW:
            continue
        output = outcome.output or {}
        if isinstance(output, dict):
            markdown = output.get("markdown")
            if isinstance(markdown, str) and markdown.strip():
                return markdown.strip()
    return None


def image_generation_failure_report_from_outcomes(outcomes: list[Any]) -> str | None:
    """#417 — a faithful, specific message when the image tool RAN but did not
    produce an image (the pipeline returned ``ok: False``).

    Returns the backend/model's own error verbatim so the user learns what
    actually happened — a real failure, the model's own refusal, a missing
    backend — instead of a generic or moralizing paraphrase from the planner.
    Returns None when no image-generation tool ran, or it succeeded.
    """
    from capabledeputy.policy.rules import Decision

    for outcome in outcomes:
        if not _is_image_generate_tool_name(outcome.tool_name):
            continue
        if outcome.decision != Decision.ALLOW:
            continue
        output = outcome.output or {}
        if not isinstance(output, dict):
            continue
        if output.get("ok") is False:
            error = str(output.get("error") or "").strip() or "the image backend reported no detail"
            return f"Image generation did not produce an image. The image tool reported: {error}"
    return None


def repair_hallucinated_image_markdown(
    content: str,
    *,
    prior_paths: frozenset[str],
    allowed_paths: frozenset[str],
    outcomes: list[Any],
) -> str:
    if not looks_like_hallucinated_image_markdown(
        content,
        prior_paths=prior_paths,
        allowed_paths=allowed_paths,
    ):
        return content
    replacement = preferred_image_markdown_from_outcomes(outcomes)
    if replacement:
        return replacement
    return content


def looks_like_image_generation_refusal(content: str) -> bool:
    """True when the model declined in prose instead of calling image.generate."""
    stripped = content.strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    if "tool_calls" in stripped:
        return False
    if any(
        token in lowered
        for token in (
            "image.generate",
            "bundled-image-generate.image.generate",
            "bundled-images.image.generate",
        )
    ):
        return False
    if _IMAGE_GENERATION_REFUSAL_RE.search(stripped):
        return True
    if "real people" in lowered or "real person" in lowered:
        return True
    return "inappropriate" in lowered or "non-consensual" in lowered


def image_generate_tool_names_in(selected_names: set[str] | frozenset[str]) -> bool:
    return any(name in selected_names for name in _image_generate_tool_names())


def _image_generate_tool_names() -> tuple[str, ...]:
    return (
        "bundled-image-generate.image.generate",
        "bundled-images.image.generate",
        "image.generate",
    )


def _image_fetch_tool_names() -> tuple[str, ...]:
    return (
        "bundled-image-fetch.image.fetch",
        "bundled-images.image.fetch",
        "image.fetch",
    )


def _wikipedia_lookup_tool_names() -> tuple[str, ...]:
    return ("bundled-fetch.wikipedia.lookup", "wikipedia.lookup")


def has_research_data_intent(message: str) -> bool:
    """True when the user wants data gathered before answering/plotting."""
    stripped = message.strip()
    return bool(stripped and re.search(r"\bresearch\b", stripped, re.IGNORECASE))


def has_web_search_intent(message: str) -> bool:
    """True when the user message is asking for web/news search."""
    stripped = message.strip()
    if not stripped:
        return False
    if _WEB_SEARCH_INTENT_RE.search(stripped):
        return True
    return has_chart_generation_intent(stripped) and has_research_data_intent(stripped)


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
    if (
        has_image_generation_intent(stripped)
        or has_image_fetch_intent(stripped)
        or has_wikipedia_lookup_intent(stripped)
    ):
        return False
    return not _TOOL_INTENT_RE.search(stripped)
