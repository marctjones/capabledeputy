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
    SkillMode,
    SkillParseError,
    load_skill_directory,
    load_skill_directory_report,
    parse_skill_package,
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
    assert skill.mode == SkillMode.TOOL
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


def test_folder_skill_defaults_to_guidance_and_discovers_resources(tmp_path: Path) -> None:
    package = tmp_path / "codex-style"
    (package / "references").mkdir(parents=True)
    (package / "scripts").mkdir()
    (package / "references" / "guide.md").write_text("Reference details")
    (package / "scripts" / "run.py").write_text("print('ok')\n")
    (package / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: codex-style
            description: Use this for a Codex-style workflow.
            metadata:
              short-description: Codex style
            scripts:
              - path: scripts/run.py
                language: python
                spec_id: python-sandbox
            ---
            # Workflow
            Read references/guide.md only when needed.
            """,
        ),
    )

    skill = parse_skill_package(package)

    assert skill.mode == SkillMode.GUIDANCE
    assert skill.guidance_enabled
    assert not skill.tool_enabled
    assert skill.metadata["short-description"] == "Codex style"
    assert {resource.relpath for resource in skill.resources} == {
        "references/guide.md",
        "scripts/run.py",
    }
    assert skill.scripts[0].relpath == "scripts/run.py"
    assert "sandbox/container" in "; ".join(skill.diagnostics)


def test_folder_skill_rejects_script_path_traversal(tmp_path: Path) -> None:
    package = tmp_path / "bad-script"
    package.mkdir()
    (package / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: bad-script
            description: bad script path
            scripts:
              - path: ../run.py
            ---
            Body
            """,
        ),
    )

    with pytest.raises(SkillParseError, match="escapes package root"):
        parse_skill_package(package)


def test_folder_skill_rejects_resource_symlink_escape(tmp_path: Path) -> None:
    package = tmp_path / "bad-resource"
    (package / "references").mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("outside")
    (package / "references" / "outside.md").symlink_to(outside)
    (package / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: bad-resource
            description: bad resource path
            ---
            Body
            """,
        ),
    )

    with pytest.raises(SkillParseError, match="escapes package root"):
        parse_skill_package(package)


def test_hybrid_folder_skill_registers_tool_and_guidance(tmp_path: Path) -> None:
    package = tmp_path / "hybrid"
    package.mkdir()
    (package / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: skill.hybrid
            description: hybrid skill
            mode: hybrid
            parameters:
              type: object
              properties:
                text:
                  type: string
            target_arg: text
            ---
            Summarize {{text}}
            """,
        ),
    )
    registry = ToolRegistry()
    report = load_skill_directory_report(tmp_path, registry, FakeLLMClient([]))

    assert "skill.hybrid" in report.skills
    assert report.skills["skill.hybrid"].guidance_enabled
    assert "skill.hybrid" in registry
    assert report.registered_tools == ["skill.hybrid"]


def test_guidance_loads_without_quarantined_llm(tmp_path: Path) -> None:
    package = tmp_path / "guidance"
    package.mkdir()
    (package / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: skill.guidance
            description: guidance skill
            ---
            Follow this workflow.
            """,
        ),
    )
    registry = ToolRegistry()
    report = load_skill_directory_report(tmp_path, registry, None)

    assert "skill.guidance" in report.skills
    assert report.skills["skill.guidance"].guidance_enabled
    assert report.registered_tools == []
    assert report.skipped == []


def test_tool_skill_without_quarantined_llm_is_visible_but_not_registered(tmp_path: Path) -> None:
    (tmp_path / "tool.md").write_text(_skill_text(name="skill.needs_llm"))
    registry = ToolRegistry()
    report = load_skill_directory_report(tmp_path, registry, None)

    assert "skill.needs_llm" in report.skills
    assert "skill.needs_llm" not in registry
    assert report.skipped == [
        {
            "path": str(tmp_path / "tool.md"),
            "name": "skill.needs_llm",
            "reason": "missing-quarantined-llm",
        },
    ]


async def test_script_skill_refuses_without_sandbox(tmp_path: Path) -> None:
    package = tmp_path / "scripted"
    (package / "scripts").mkdir(parents=True)
    (package / "scripts" / "run.py").write_text("print('ok')\n")
    (package / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: skill.scripted
            description: scripted skill
            mode: tool
            scripts:
              - path: scripts/run.py
                language: python
                spec_id: python-sandbox
            ---
            Script body
            """,
        ),
    )
    skill = parse_skill_package(package)
    tool = skill_to_tool(skill, FakeLLMClient([]))
    from uuid import uuid4

    assert tool.capability_kind == CapabilityKind.EXECUTE_SANDBOX
    assert tool.target_arg == "spec_id"
    result = await tool.handler({}, ToolContext(session_id=uuid4(), label_state=LabelState()))

    assert "host execution is refused" in result.output["error"]


class _SandboxResult:
    exit_code = 0
    output_digest = "abc123"
    cancelled = False
    timed_out = False
    outputs = ()


class _FakeSandboxActuator:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create_region(self, *, spec_id: str) -> str:
        self.calls.append({"method": "create_region", "spec_id": spec_id})
        return "region-1"

    def execute(self, **kwargs: Any) -> _SandboxResult:
        self.calls.append({"method": "execute", **kwargs})
        return _SandboxResult()

    def discard_region(self, region_id: str) -> None:
        self.calls.append({"method": "discard_region", "region_id": region_id})


async def test_script_skill_runs_only_through_sandbox(tmp_path: Path) -> None:
    package = tmp_path / "scripted"
    (package / "scripts").mkdir(parents=True)
    (package / "scripts" / "run.py").write_text("print('ok')\n")
    (package / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: skill.scripted
            description: scripted skill
            mode: tool
            scripts:
              - path: scripts/run.py
                language: python
                spec_id: python-sandbox
            ---
            Script body
            """,
        ),
    )
    skill = parse_skill_package(package)
    actuator = _FakeSandboxActuator()
    tool = skill_to_tool(skill, FakeLLMClient([]), sandbox_actuator=actuator)  # type: ignore[arg-type]
    from uuid import uuid4

    result = await tool.handler({}, ToolContext(session_id=uuid4(), label_state=LabelState()))

    assert result.output["exit_code"] == 0
    execute = next(call for call in actuator.calls if call["method"] == "execute")
    assert execute["argv"] == ("python", "/in/main.py")
    assert execute["inputs"] == {"main.py": b"print('ok')\n"}


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
