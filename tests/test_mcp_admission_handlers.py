from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.app import App
from capabledeputy.daemon.mcp_admission_handlers import make_mcp_admission_handlers


@pytest.fixture
async def app(tmp_path: Path) -> App:
    instance = App(state_db_path=tmp_path / "state.db", audit_log_path=tmp_path / "audit.jsonl")
    await instance.startup()
    return instance


async def test_mcp_admission_preview_approve_disable_and_audit(app: App) -> None:
    handlers = make_mcp_admission_handlers(app)

    preview = await handlers["mcp.admission.preview"](
        {
            "server": "github",
            "tools": [
                {"name": "list_issues", "annotations": {"readOnlyHint": True}},
                {"name": "do_everything"},
            ],
            "actor": "tester",
        },
    )

    assert preview["admitted_count"] == 1
    listed = await handlers["mcp.admission.list"]({"server": "github"})
    statuses = {tool["name"]: tool["status"] for tool in listed["tools"]}
    assert statuses == {"do_everything": "refused", "list_issues": "previewed"}

    approved = await handlers["mcp.admission.approve"](
        {"server": "github", "tools": ["list_issues"], "approved_by": "alice"},
    )
    assert approved["tools"][0]["status"] == "approved"
    assert approved["tools"][0]["approved_by"] == "alice"

    disabled = await handlers["mcp.admission.disable"](
        {"server": "github", "tools": ["list_issues"], "disabled_by": "alice"},
    )
    assert disabled["tools"][0]["status"] == "disabled"

    audit = await handlers["mcp.admission.audit"]({"server": "github"})
    assert [event["action"] for event in audit["events"][:3]] == [
        "disabled",
        "approved",
        "preview",
    ]


async def test_mcp_admission_requires_reapproval_when_mapping_changes(app: App) -> None:
    handlers = make_mcp_admission_handlers(app)
    await handlers["mcp.admission.preview"](
        {
            "config": {
                "name": "notion",
                "command": ["noop"],
                "tool_overrides": {
                    "search": {"capability_kind": "READ_FS", "target_arg": "query"},
                },
            },
            "tools": [{"name": "search"}],
        },
    )
    await handlers["mcp.admission.approve"](
        {"server": "notion", "tools": ["search"], "approved_by": "alice"},
    )

    changed = await handlers["mcp.admission.preview"](
        {
            "config": {
                "name": "notion",
                "command": ["noop"],
                "tool_overrides": {
                    "search": {"capability_kind": "WEB_FETCH", "target_arg": "query"},
                },
            },
            "tools": [{"name": "search"}],
        },
    )

    assert changed["decisions"][0]["status"] == "needs_reapproval"


async def test_mcp_admission_persists_in_state_db(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    first = App(state_db_path=path, audit_log_path=tmp_path / "audit1.jsonl")
    await first.startup()
    handlers = make_mcp_admission_handlers(first)
    await handlers["mcp.admission.preview"](
        {
            "server": "files",
            "tools": [{"name": "read_file", "annotations": {"readOnlyHint": True}}],
        },
    )

    second = App(state_db_path=path, audit_log_path=tmp_path / "audit2.jsonl")
    await second.startup()
    persisted = await make_mcp_admission_handlers(second)["mcp.admission.list"]({"server": "files"})

    assert persisted["tools"][0]["name"] == "read_file"
    assert persisted["tools"][0]["status"] == "previewed"
