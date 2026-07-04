"""SKILL.md adapter — imports markdown skills into CapDep.

CapDep supports its original flat Markdown skill files plus folder-based
`SKILL.md` packages. Imported skills are normalized into explicit guidance,
tool, or hybrid modes so compatibility does not bypass the policy model.

When invoked the skill calls the quarantined LLM (no tools provided),
either as a free-text generator (output = `{"text": ...}`) or — when
`schema:` is set in the frontmatter — through the structured-extraction
path so the output is a typed, schema-validated value. Either way the
declared `inherent_tags` propagate into the calling session's label
set, identical to native tools.
"""

from __future__ import annotations

from capabledeputy.skills.adapter import skill_to_tool
from capabledeputy.skills.loader import (
    SkillLoadReport,
    load_skill_directory,
    load_skill_directory_report,
)
from capabledeputy.skills.parser import (
    Skill,
    SkillMode,
    SkillParseError,
    SkillResource,
    SkillScript,
    parse_skill_package,
    parse_skill_text,
)

__all__ = [
    "Skill",
    "SkillLoadReport",
    "SkillMode",
    "SkillParseError",
    "SkillResource",
    "SkillScript",
    "load_skill_directory",
    "load_skill_directory_report",
    "parse_skill_package",
    "parse_skill_text",
    "skill_to_tool",
]
