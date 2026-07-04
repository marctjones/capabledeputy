from __future__ import annotations

import textwrap
from pathlib import Path

from capabledeputy.app import App
from capabledeputy.daemon.session_handlers import make_session_handlers
from capabledeputy.daemon.skill_handlers import make_skill_handlers
from capabledeputy.llm.fake import FakeLLMClient


def _write_guidance_skill(root: Path) -> None:
    package = root / "guide"
    package.mkdir()
    (package / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: guide
            description: guidance skill
            ---
            # Guidance
            Follow this workflow.
            """,
        ),
    )


async def test_skill_handlers_list_show_and_diagnostics(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_guidance_skill(skills_dir)
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        quarantined_llm=FakeLLMClient([]),
        skills_dir=skills_dir,
    )
    await app.startup()
    handlers = make_skill_handlers(app)

    listed = await handlers["skill.list"]({})
    shown = await handlers["skill.show"]({"name": "guide", "include_body": True})
    diagnostics = await handlers["skill.diagnostics"]({})

    assert listed["skills"][0]["name"] == "guide"
    assert listed["skills"][0]["mode"] == "guidance"
    assert listed["registered_tools"] == []
    assert "# Guidance" in shown["body"]
    assert diagnostics["loaded_count"] == 1
    assert diagnostics["invalid"] == []


async def test_skill_guidance_is_audited(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_guidance_skill(skills_dir)
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        quarantined_llm=FakeLLMClient([]),
        skills_dir=skills_dir,
    )
    await app.startup()
    sessions = make_session_handlers(app.graph)
    session = await sessions["session.new"]({"intent": "skill guidance"})
    handlers = make_skill_handlers(app)

    guidance = await handlers["skill.guidance"](
        {"name": "guide", "session_id": session["id"]},
    )

    assert guidance["security"].startswith("untrusted guidance")
    assert "Follow this workflow" in guidance["body"]
    audit_text = (tmp_path / "audit.jsonl").read_text()
    assert "skill.guidance_loaded" in audit_text
    assert "guide" in audit_text
