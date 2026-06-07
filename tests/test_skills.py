"""SKILL.md adapter: parser, body rendering, tool wrapping, loader.

Skills become labeled tools whose handler calls the quarantined LLM.
The frontmatter declares labels + capability kind, so every guarantee
the existing native tools have (label propagation, capability gating,
audit emission) applies to skills automatically.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest

from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import FinishReason, LLMResponse
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.labels import CategoryTag, LabelState, Tier
from capabledeputy.skills import (
    Skill,
    SkillParseError,
    load_skill_directory,
    parse_skill_text,
    skill_to_tool,
)
from capabledeputy.tools.registry import ToolContext, ToolRegistry


def _skill_text(**overrides: Any) -> str:
    front: dict[str, Any] = {
        "name": "skill.test",
        "description": "test skill",
        "capability_kind": "READ_FS",
        "inherent_tags": {
            "a": [
                {
                    "category": "health",
                    "tier": "regulated",
                    "assignment_provenance": "source-declared",
                }
            ],
            "b": [],
        },
        "target_arg": "text",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}}},
    }
    front.update(overrides)
    import yaml

    front_yaml = yaml.safe_dump(front)
    body = "Summarize: {{text}}\n"
    return f"---\n{front_yaml}---\n{body}"


def test_parser_reads_required_fields() -> None:
    skill = parse_skill_text(_skill_text())
    assert skill.name == "skill.test"
    assert skill.description == "test skill"
    assert skill.capability_kind == CapabilityKind.READ_FS
    assert (
        CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared")
        in skill.inherent_tags.a
    )


def test_parser_renders_placeholders() -> None:
    skill = parse_skill_text(_skill_text())
    rendered = skill.render({"text": "lisinopril 10mg daily"})
    assert "Summarize: lisinopril 10mg daily" in rendered


def test_parser_missing_name_errors() -> None:
    with pytest.raises(SkillParseError, match="name"):
        parse_skill_text("---\ndescription: x\n---\nbody\n")


def test_parser_missing_frontmatter_errors() -> None:
    with pytest.raises(SkillParseError, match="frontmatter"):
        parse_skill_text("just a body\n")


def test_parser_unknown_label_errors() -> None:
    text = textwrap.dedent(
        """\
        ---
        name: skill.bad
        description: x
        inherent_tags:
          a:
            - invalid_tag_structure
        ---
        body
        """,
    )
    with pytest.raises(SkillParseError, match="tags"):
        parse_skill_text(text)


def test_parser_unknown_capability_kind_errors() -> None:
    text = textwrap.dedent(
        """\
        ---
        name: skill.bad
        description: x
        capability_kind: NOT_REAL
        ---
        body
        """,
    )
    with pytest.raises(SkillParseError, match="capability_kind"):
        parse_skill_text(text)


async def test_skill_to_tool_returns_text_with_inherent_labels() -> None:
    skill = parse_skill_text(_skill_text())
    llm = FakeLLMClient(
        [LLMResponse(content="lisinopril 10mg daily.", finish_reason=FinishReason.STOP)],
    )
    tool = skill_to_tool(skill, llm)

    from uuid import uuid4

    ctx = ToolContext(session_id=uuid4(), label_state=LabelState())
    result = await tool.handler({"text": "raw prescription"}, ctx)
    assert result.output == {"text": "lisinopril 10mg daily."}
    assert (
        CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared")
        in result.additional_tags.a
    )


async def test_skill_to_tool_refuses_tool_call_emission() -> None:
    """A misbehaving quarantined LLM must not be able to produce a tool
    call through a skill — the skill body must produce text only."""
    from capabledeputy.llm.types import ToolCall

    skill = parse_skill_text(_skill_text())
    llm = FakeLLMClient(
        [
            LLMResponse(
                content="",
                tool_calls=(ToolCall(id="x", name="something", args={}),),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
        ],
    )
    tool = skill_to_tool(skill, llm)
    from uuid import uuid4

    ctx = ToolContext(session_id=uuid4(), label_state=LabelState())
    result = await tool.handler({"text": "x"}, ctx)
    assert "error" in result.output
    assert "tool_calls" in result.output["error"]


async def test_skill_to_tool_with_schema_uses_extractor() -> None:
    """A skill with schema=DoseSummary returns a typed dict via the
    quarantined extractor path. Output is the schema's fields, not text."""
    text = textwrap.dedent(
        """\
        ---
        name: skill.extract_dose_test
        description: extract dose
        capability_kind: READ_FS
        schema: DoseSummary
        target_arg: text
        ---
        Extract from: {{text}}
        """,
    )
    skill = parse_skill_text(text)
    llm = FakeLLMClient(
        [
            LLMResponse(
                content=(
                    '{"medication_name": "lisinopril", "dosage_mg": 10.0, "frequency": "daily"}'
                ),
                finish_reason=FinishReason.STOP,
            ),
        ],
    )
    tool = skill_to_tool(skill, llm)
    from uuid import uuid4

    ctx = ToolContext(session_id=uuid4(), label_state=LabelState())
    result = await tool.handler({"text": "lisinopril 10mg daily"}, ctx)
    assert result.output["medication_name"] == "lisinopril"
    assert result.output["dosage_mg"] == 10.0
    assert result.output["frequency"] == "daily"


def test_load_skill_directory_registers_files(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text(_skill_text(name="skill.a"))
    (tmp_path / "b.md").write_text(_skill_text(name="skill.b"))
    registry = ToolRegistry()
    llm = FakeLLMClient([])
    names = load_skill_directory(tmp_path, registry, llm)
    assert names == ["skill.a", "skill.b"]
    assert "skill.a" in registry
    assert "skill.b" in registry


def test_load_skill_directory_dedups_when_requested(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text(_skill_text(name="skill.dup"))
    (tmp_path / "b.md").write_text(_skill_text(name="skill.dup"))
    registry = ToolRegistry()
    llm = FakeLLMClient([])
    names = load_skill_directory(tmp_path, registry, llm, skip_on_duplicate=True)
    assert names == ["skill.dup"]


def test_repo_starter_skills_parse() -> None:
    """The skills/ directory at the repo root must contain valid SKILL.md
    files so the starter pack stays buildable."""
    skills_dir = Path(__file__).parent.parent / "skills"
    if not skills_dir.is_dir():
        pytest.skip("no skills directory in repo")
    for path in sorted(skills_dir.glob("*.md")):
        skill = parse_skill_text(path.read_text(encoding="utf-8"), source_path=path)
        assert isinstance(skill, Skill)
        assert skill.name.startswith("skill.")
