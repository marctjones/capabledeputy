from __future__ import annotations

import json

import pytest

from capabledeputy.daemon.scripting_handlers import make_scripting_handlers


async def test_scripting_plan_returns_daemon_owned_workflow(tmp_path) -> None:
    handlers = make_scripting_handlers()

    result = await handlers["scripting.plan"](
        {
            "task": "Batch rename photos",
            "workspace_root": str(tmp_path),
            "workspace_id": "photos",
            "language": "python",
            "target_path": "rename_photos.py",
        }
    )

    workflow = result["workflow"]
    assert workflow["script_destination_id"] == "script-workspace:photos:rename_photos.py"
    assert [step["artifact_type"] for step in workflow["steps"]] == [
        "script",
        "script_run",
        "file_export",
    ]


async def test_scripting_prepare_script_returns_review_artifact(tmp_path) -> None:
    handlers = make_scripting_handlers()

    result = await handlers["scripting.prepare_script"](
        {
            "workspace_root": str(tmp_path),
            "workspace_id": "photos",
            "target_path": "rename_photos.py",
            "language": "python",
            "code": "print('rename')\n",
        }
    )

    assert result["artifact"]["artifact_type"] == "script"
    assert result["review_artifact"]["destination_id"] == "script-workspace:photos:rename_photos.py"
    assert result["review_artifact"]["preview"] == "print('rename')\n"


async def test_scripting_run_and_export_artifacts_are_typed(tmp_path) -> None:
    handlers = make_scripting_handlers()

    run = await handlers["scripting.run_artifact"](
        {
            "workspace_root": str(tmp_path),
            "workspace_id": "photos",
            "run_result": {
                "spec_id": "run-1",
                "exit_code": 0,
                "output_digest": "sha256:abc",
                "outputs": [{"path": "/out/report.txt", "sha256": "abc", "size": 3}],
            },
        }
    )
    export = await handlers["scripting.export_artifact"](
        {
            "workspace_root": str(tmp_path),
            "workspace_id": "photos",
            "target_path": "out/report.txt",
            "content": "renamed 3 files\n",
        }
    )

    assert run["artifact"]["artifact_type"] == "script_run"
    assert json.loads(run["artifact"]["content"])["exit_code"] == 0
    assert export["artifact"]["artifact_type"] == "file_export"
    assert export["review_artifact"]["destination_id"] == "script-workspace:photos:out/report.txt"


async def test_scripting_handlers_fail_closed_on_escaping_export(tmp_path) -> None:
    handlers = make_scripting_handlers()

    with pytest.raises(RuntimeError, match="escapes"):
        await handlers["scripting.export_artifact"](
            {
                "workspace_root": str(tmp_path),
                "target_path": "../escape.txt",
                "content": "no",
            }
        )
