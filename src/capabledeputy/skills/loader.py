"""Load every SKILL.md in a directory into the tool registry."""

from __future__ import annotations

from pathlib import Path

from capabledeputy.llm.client import LLMClient
from capabledeputy.skills.adapter import skill_to_tool
from capabledeputy.skills.parser import parse_skill_file
from capabledeputy.tools.registry import DuplicateToolError, ToolRegistry


def load_skill_directory(
    directory: Path,
    registry: ToolRegistry,
    llm: LLMClient,
    *,
    pattern: str = "*.md",
    skip_on_duplicate: bool = False,
) -> list[str]:
    """Register every skill in `directory` (matching `pattern`) into `registry`.

    Returns the list of registered tool names, in the order encountered.
    If `skip_on_duplicate` is True, a name collision quietly skips the
    second skill rather than raising; otherwise the upstream
    DuplicateToolError propagates so misconfigurations are loud.
    """
    if not directory.is_dir():
        raise FileNotFoundError(f"skill directory not found: {directory}")

    registered: list[str] = []
    for path in sorted(directory.glob(pattern)):
        skill = parse_skill_file(path)
        tool = skill_to_tool(skill, llm)
        try:
            registry.register(tool)
        except DuplicateToolError:
            if skip_on_duplicate:
                continue
            raise
        registered.append(skill.name)
    return registered
