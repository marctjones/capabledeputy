"""Deterministic + optional AI tool-surface curation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from capabledeputy.agent.chat_turn import (
    _image_fetch_tool_names,
    _image_generate_tool_names,
    _wikipedia_lookup_tool_names,
    has_chart_generation_intent,
    has_image_fetch_intent,
    has_image_generation_intent,
    has_web_search_intent,
    has_wikipedia_lookup_intent,
)
from capabledeputy.agent.tool_families import (
    ToolFamiliesConfig,
    family_for_purpose,
    load_tool_families,
    tool_matches_family,
)
from capabledeputy.llm.models_config import ToolSelectionConfig
from capabledeputy.llm.types import Message, Role
from capabledeputy.mode.dispatcher import ExecutionMode
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.session.model import Session
from capabledeputy.tools.registry import ToolDefinition, ToolRegistry

if TYPE_CHECKING:
    from capabledeputy.llm.client import LLMClient


@dataclass(frozen=True)
class ToolSelectionResult:
    selected: tuple[ToolDefinition, ...]
    candidates: tuple[ToolDefinition, ...]
    n_visible: int
    method: str
    mandatory_added: tuple[str, ...] = field(default_factory=tuple)
    scores: dict[str, float] = field(default_factory=dict)


def _tokenize(text: str) -> set[str]:
    return {t.lower() for t in re.findall(r"[a-zA-Z][a-zA-Z0-9_.-]{1,}", text) if len(t) > 2}


def _compact_line(tool: ToolDefinition) -> str:
    return f"{tool.name}: {tool.description}"


def _score_tool(tool: ToolDefinition, query_tokens: set[str], family: bool) -> float:
    hay = f"{tool.name} {tool.description}".lower()
    score = 0.0
    for token in query_tokens:
        if token in hay:
            score += 2.0
        if token in tool.name.lower():
            score += 3.0
    if family:
        score += 5.0
    return score


_KAGI_SEARCH_TOOL = "kagi.kagi_search_fetch"
_LEGACY_DDG_SEARCH_TOOLS = frozenset({"web.search", "bundled-search.search.web"})


def _kagi_search_available(tools: list[ToolDefinition]) -> bool:
    return any(t.name == _KAGI_SEARCH_TOOL for t in tools)


def _prefer_kagi_over_ddg_fallbacks(tools: list[ToolDefinition]) -> list[ToolDefinition]:
    """When Kagi is registered, hide native/MCP DuckDuckGo search tools.

    The model often picks the familiar ``web.search`` name even though it
    never uses the Kagi API key — only the keyed Kagi MCP tool does.
    """
    if not _kagi_search_available(tools):
        return tools
    filtered = [t for t in tools if t.name not in _LEGACY_DDG_SEARCH_TOOLS]
    return filtered or tools


def _tools_for_granted_kinds(session: Session, tools: list[ToolDefinition]) -> set[str]:
    granted_kinds: set[CapabilityKind] = {cap.kind for cap in session.capability_set}
    if not granted_kinds:
        return set()
    return {t.name for t in tools if t.capability_kind in granted_kinds}


def _mode_required_tools(
    tools: list[ToolDefinition],
    mode: ExecutionMode,
) -> set[str]:
    if mode == ExecutionMode.DUAL_LLM:
        return {t.name for t in tools if t.name.startswith("quarantined.")}
    return set()


def _apply_family_filter(
    visible: list[ToolDefinition],
    families: ToolFamiliesConfig,
    purpose_handle: str,
) -> list[ToolDefinition]:
    family = family_for_purpose(families, purpose_handle)
    if family is None:
        return visible
    if not family.include_kinds and not family.include_prefixes:
        return visible
    narrowed = [t for t in visible if tool_matches_family(t, family)]
    return narrowed or visible


def _retrieval_rank(
    candidates: list[ToolDefinition],
    *,
    user_message: str,
    purpose_handle: str,
    families: ToolFamiliesConfig,
    top_k: int,
) -> list[tuple[ToolDefinition, float]]:
    query_tokens = _tokenize(user_message)
    family = family_for_purpose(families, purpose_handle)
    scored: list[tuple[ToolDefinition, float]] = []
    for tool in candidates:
        in_family = family is not None and tool_matches_family(tool, family)
        scored.append((tool, _score_tool(tool, query_tokens, in_family)))
    scored.sort(key=lambda pair: (-pair[1], pair[0].name))
    return scored[:top_k]


async def _ai_top_k(
    router_llm: LLMClient,
    compact_lines: list[str],
    *,
    top_k: int,
) -> list[str] | None:
    prompt = (
        "Select the tools needed for the user request. Respond with ONLY JSON:\n"
        f'{{"tools": ["tool.name", ...]}}\n'
        f"Pick at most {top_k} tools from this list:\n"
        + "\n".join(f"- {line}" for line in compact_lines)
        + "\n\nUser request follows in the conversation."
    )
    try:
        response = await router_llm.respond(
            [Message(role=Role.USER, content=prompt)],
            [],
        )
    except Exception:
        return None
    text = (response.content or "").strip()
    start = text.find("{")
    if start == -1:
        return None
    try:
        payload = json.loads(text[start : text.rfind("}") + 1])
    except json.JSONDecodeError:
        return None
    raw = payload.get("tools")
    if not isinstance(raw, list):
        return None
    return [str(name) for name in raw if isinstance(name, str)]


def select_tools_for_turn(
    registry: ToolRegistry,
    session: Session,
    mode: ExecutionMode,
    visible: list[ToolDefinition],
    *,
    user_message: str,
    families: ToolFamiliesConfig | None = None,
    selection_config: ToolSelectionConfig | None = None,
) -> ToolSelectionResult:
    families_cfg = families or load_tool_families()
    cfg = selection_config or ToolSelectionConfig()
    n_visible = len(visible)

    if cfg.mode == "static" or n_visible <= cfg.max_selected:
        return ToolSelectionResult(
            selected=tuple(visible),
            candidates=tuple(visible),
            n_visible=n_visible,
            method="full_surface",
        )

    narrowed = _apply_family_filter(visible, families_cfg, session.purpose_handle)
    narrowed = _prefer_kagi_over_ddg_fallbacks(narrowed)
    method = "family+retrieve"
    ranked = _retrieval_rank(
        narrowed,
        user_message=user_message,
        purpose_handle=session.purpose_handle,
        families=families_cfg,
        top_k=cfg.retrieval_top_k,
    )
    candidates = [tool for tool, _ in ranked]
    candidate_names = {t.name for t in candidates}

    mandatory_names: set[str] = set(families_cfg.mandatory_always)
    mandatory_names.update(_mode_required_tools(visible, mode))
    if has_web_search_intent(user_message) and not has_image_generation_intent(user_message):
        if _kagi_search_available(visible):
            mandatory_names.add(_KAGI_SEARCH_TOOL)
        else:
            for name in _LEGACY_DDG_SEARCH_TOOLS:
                if any(t.name == name for t in visible):
                    mandatory_names.add(name)
    visible_names = {t.name for t in visible}
    if has_image_fetch_intent(user_message) or has_wikipedia_lookup_intent(user_message):
        for tool_name in _wikipedia_lookup_tool_names():
            if tool_name in visible_names:
                mandatory_names.add(tool_name)
                break
        if has_image_fetch_intent(user_message):
            for tool_name in _image_fetch_tool_names():
                if tool_name in visible_names:
                    mandatory_names.add(tool_name)
                    break
    elif has_image_generation_intent(user_message):
        for tool_name in _image_generate_tool_names():
            if tool_name in visible_names:
                mandatory_names.add(tool_name)
                break
    if has_chart_generation_intent(user_message) and any(t.name == "chart.plot" for t in visible):
        mandatory_names.add("chart.plot")
    for name in mandatory_names:
        if any(t.name == name for t in visible):
            candidate_names.add(name)

    selected_map: dict[str, ToolDefinition] = {}
    scores: dict[str, float] = {}
    mandatory_added: list[str] = []
    visible_by_name = {t.name: t for t in visible}
    for name in sorted(mandatory_names):
        if len(selected_map) >= cfg.max_selected:
            break
        tool = visible_by_name.get(name)
        if tool is None:
            continue
        selected_map[name] = tool
        mandatory_added.append(name)

    for tool, score in ranked:
        if len(selected_map) >= cfg.max_selected:
            break
        if tool.name in selected_map:
            scores[tool.name] = score
            continue
        selected_map[tool.name] = tool
        scores[tool.name] = score

    selected = tuple(sorted(selected_map.values(), key=lambda t: t.name))
    return ToolSelectionResult(
        selected=selected,
        candidates=tuple(candidates),
        n_visible=n_visible,
        method=method,
        mandatory_added=tuple(mandatory_added),
        scores=scores,
    )


async def select_tools_for_turn_async(
    registry: ToolRegistry,
    session: Session,
    mode: ExecutionMode,
    visible: list[ToolDefinition],
    *,
    user_message: str,
    router_llm: LLMClient | None = None,
    families: ToolFamiliesConfig | None = None,
    selection_config: ToolSelectionConfig | None = None,
) -> ToolSelectionResult:
    base = select_tools_for_turn(
        registry,
        session,
        mode,
        visible,
        user_message=user_message,
        families=families,
        selection_config=selection_config,
    )
    cfg = selection_config or ToolSelectionConfig()
    if cfg.mode != "retrieve+ai" or router_llm is None or base.n_visible < cfg.ai_gate_visible_gte:
        return base

    compact = [_compact_line(t) for t in base.candidates]
    ai_names = await _ai_top_k(router_llm, compact, top_k=cfg.ai_top_k)
    if not ai_names:
        return base

    visible_by_name = {t.name: t for t in visible}
    selected_map: dict[str, ToolDefinition] = {
        name: visible_by_name[name] for name in ai_names if name in visible_by_name
    }
    for name in base.mandatory_added:
        tool = visible_by_name.get(name)
        if tool is not None:
            selected_map[name] = tool

    if not selected_map:
        return base

    capped = tuple(
        sorted(selected_map.values(), key=lambda t: t.name)[: cfg.max_selected],
    )
    return ToolSelectionResult(
        selected=capped,
        candidates=base.candidates,
        n_visible=base.n_visible,
        method="family+retrieve+ai",
        mandatory_added=base.mandatory_added,
        scores=base.scores,
    )


def widen_tool_surface(
    current: ToolSelectionResult,
    visible: list[ToolDefinition],
    *,
    missing_tool_name: str | None = None,
) -> ToolSelectionResult:
    names = {t.name for t in current.selected}
    expanded = list(current.selected)
    for tool in visible:
        if tool.name not in names:
            expanded.append(tool)
            names.add(tool.name)
    if missing_tool_name:
        visible_by_name = {t.name: t for t in visible}
        tool = visible_by_name.get(missing_tool_name)
        if tool is not None and tool.name not in names:
            expanded.append(tool)
    return ToolSelectionResult(
        selected=tuple(sorted(expanded, key=lambda t: t.name)),
        candidates=tuple(visible),
        n_visible=len(visible),
        method="widened",
        mandatory_added=current.mandatory_added,
        scores=current.scores,
    )
