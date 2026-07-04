"""Load every SKILL.md in a directory into the tool registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from capabledeputy.llm.client import LLMClient
from capabledeputy.skills.adapter import skill_to_tool
from capabledeputy.skills.parser import (
    Skill,
    SkillParseError,
    parse_skill_file,
    parse_skill_package,
)
from capabledeputy.tools.registry import DuplicateToolError, ToolRegistry


@dataclass
class SkillLoadReport:
    skills: dict[str, Skill] = field(default_factory=dict)
    registered_tools: list[str] = field(default_factory=list)
    invalid: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skills": [skill.to_summary() for skill in self.skills.values()],
            "registered_tools": list(self.registered_tools),
            "invalid": list(self.invalid),
            "skipped": list(self.skipped),
        }


def load_skill_directory(
    directory: Path,
    registry: ToolRegistry,
    llm: LLMClient,
    *,
    pattern: str = "*.md",
    skip_on_duplicate: bool = False,
    sandbox_actuator: Any = None,
    audit: Any = None,
) -> list[str]:
    """Register every skill in `directory` (matching `pattern`) into `registry`.

    Returns the list of registered tool names, in the order encountered.
    If `skip_on_duplicate` is True, a name collision quietly skips the
    second skill rather than raising; otherwise the upstream
    DuplicateToolError propagates so misconfigurations are loud.
    """
    if not directory.is_dir():
        raise FileNotFoundError(f"skill directory not found: {directory}")

    return load_skill_directory_report(
        directory,
        registry,
        llm,
        pattern=pattern,
        skip_on_duplicate=skip_on_duplicate,
        sandbox_actuator=sandbox_actuator,
        audit=audit,
    ).registered_tools


def load_skill_directory_report(
    directory: Path,
    registry: ToolRegistry,
    llm: LLMClient | None,
    *,
    pattern: str = "*.md",
    skip_on_duplicate: bool = False,
    sandbox_actuator: Any = None,
    audit: Any = None,
) -> SkillLoadReport:
    """Load flat CapDep skills and folder-based SKILL.md packages.

    Flat files keep their historical default of `mode: tool`. Folder packages
    default to `mode: guidance` so Codex/Claude-style skills are imported as
    untrusted procedural context unless they explicitly opt into tool/hybrid.
    """
    if not directory.is_dir():
        raise FileNotFoundError(f"skill directory not found: {directory}")

    report = SkillLoadReport()
    candidates: list[tuple[Path, bool]] = []
    candidates.extend((path, False) for path in sorted(directory.glob(pattern)) if path.is_file())
    candidates.extend(
        (path / "SKILL.md", True)
        for path in sorted(directory.iterdir())
        if path.is_dir() and (path / "SKILL.md").is_file()
    )

    seen_paths: set[Path] = set()
    for path, is_package in candidates:
        resolved = path.resolve()
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        try:
            skill = parse_skill_package(path.parent) if is_package else parse_skill_file(path)
        except SkillParseError as e:
            report.invalid.append({"path": str(path), "error": str(e)})
            continue

        if skill.name in report.skills:
            report.skipped.append({"path": str(path), "name": skill.name, "reason": "duplicate"})
            if not skip_on_duplicate:
                raise DuplicateToolError(f"skill already loaded: {skill.name}")
            continue
        report.skills[skill.name] = skill

        if not skill.tool_enabled:
            continue
        if llm is None and not skill.scripts:
            report.skipped.append(
                {
                    "path": str(path),
                    "name": skill.name,
                    "reason": "missing-quarantined-llm",
                },
            )
            continue
        tool = skill_to_tool(
            skill,
            llm,
            sandbox_actuator=sandbox_actuator,
            audit=audit,
        )
        try:
            registry.register(tool)
        except DuplicateToolError:
            report.skipped.append(
                {"path": str(path), "name": skill.name, "reason": "duplicate-tool"},
            )
            if not skip_on_duplicate:
                raise
            continue
        report.registered_tools.append(skill.name)

    return report
