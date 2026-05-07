"""SKILL.md adapter — turns OpenClaw-style markdown skills into labeled tools.

A skill is a single Markdown file with optional YAML frontmatter followed
by a prompt body. The frontmatter declares the skill's identity, the
labels it inherits, the capability kind it requires, and optionally a
declassification schema. The body is a prompt template; `{{var}}`
placeholders are filled from the tool's call args.

When invoked the skill calls the quarantined LLM (no tools provided),
either as a free-text generator (output = `{"text": ...}`) or — when
`schema:` is set in the frontmatter — through the structured-extraction
path so the output is a typed, schema-validated value. Either way the
declared `inherent_labels` propagate into the calling session's label
set, identical to native tools.
"""

from __future__ import annotations

from capabledeputy.skills.adapter import skill_to_tool
from capabledeputy.skills.loader import load_skill_directory
from capabledeputy.skills.parser import Skill, SkillParseError, parse_skill_text

__all__ = [
    "Skill",
    "SkillParseError",
    "load_skill_directory",
    "parse_skill_text",
    "skill_to_tool",
]
