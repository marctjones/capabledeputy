"""Daemon-owned safe scripting workflow handlers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from capabledeputy.artifacts import artifact_review_card
from capabledeputy.daemon.handlers import Handler
from capabledeputy.substrate.script_workspace import (
    ScriptWorkspaceSourcePort,
    make_file_export_artifact,
    make_script_artifact,
    make_script_run_artifact,
)

_SUPPORTED_LANGUAGES = ("python", "sh", "node")


def make_scripting_handlers() -> dict[str, Handler]:
    async def plan(params: dict[str, Any]) -> dict[str, Any]:
        task = str(params.get("task") or params.get("prompt") or "").strip()
        if not task:
            raise ValueError("scripting.plan requires a non-empty task")
        workspace = _workspace(params)
        language = _language(params)
        target_path = str(params.get("target_path") or _default_script_name(language))
        return {
            "workflow": {
                "task": task,
                "workspace_id": workspace.workspace_id,
                "workspace_root": str(workspace.root),
                "language": language,
                "target_path": target_path,
                "script_destination_id": workspace.canonical_destination_id(target_path),
                "steps": [
                    {
                        "id": "review-script",
                        "title": "Review generated script",
                        "artifact_type": "script",
                    },
                    {
                        "id": "run-sandbox",
                        "title": "Run script in an isolated sandbox",
                        "artifact_type": "script_run",
                    },
                    {
                        "id": "review-export",
                        "title": "Review proposed file exports",
                        "artifact_type": "file_export",
                    },
                ],
            }
        }

    async def prepare_script(params: dict[str, Any]) -> dict[str, Any]:
        workspace = _workspace(params)
        artifact = make_script_artifact(
            title=str(params.get("title") or "Generated script"),
            code=str(params.get("code") or ""),
            language=_language(params),
            workspace=workspace,
            target_path=str(params.get("target_path") or _default_script_name(_language(params))),
        )
        return {"artifact": artifact.to_dict(), "review_artifact": artifact_review_card(artifact)}

    async def run_artifact(params: dict[str, Any]) -> dict[str, Any]:
        workspace = _workspace(params)
        result = params.get("run_result") or params.get("result") or {}
        if not isinstance(result, dict):
            raise ValueError("scripting.run_artifact requires run_result to be a mapping")
        artifact = make_script_run_artifact(
            title=str(params.get("title") or "Sandbox script run"),
            run_result=result,
            workspace=workspace,
        )
        return {"artifact": artifact.to_dict(), "review_artifact": artifact_review_card(artifact)}

    async def export_artifact(params: dict[str, Any]) -> dict[str, Any]:
        workspace = _workspace(params)
        artifact = make_file_export_artifact(
            title=str(params.get("title") or "Script output export"),
            content=str(params.get("content") or ""),
            workspace=workspace,
            target_path=str(params.get("target_path") or ""),
            content_type=str(params.get("content_type") or "text/plain"),
        )
        return {"artifact": artifact.to_dict(), "review_artifact": artifact_review_card(artifact)}

    return {
        "scripting.plan": plan,
        "scripting.prepare_script": prepare_script,
        "scripting.run_artifact": run_artifact,
        "scripting.export_artifact": export_artifact,
    }


def _workspace(params: dict[str, Any]) -> ScriptWorkspaceSourcePort:
    root = params.get("workspace_root") or params.get("root")
    if not root:
        raise ValueError("safe scripting RPC requires workspace_root")
    return ScriptWorkspaceSourcePort(
        Path(str(root)),
        workspace_id=str(params.get("workspace_id") or "local"),
    )


def _language(params: dict[str, Any]) -> str:
    language = str(params.get("language") or "python").strip().lower()
    if language == "shell":
        language = "sh"
    if language == "javascript":
        language = "node"
    if language not in _SUPPORTED_LANGUAGES:
        raise ValueError(f"unsupported scripting language {language!r}")
    return language


def _default_script_name(language: str) -> str:
    match language:
        case "python":
            return "script.py"
        case "sh":
            return "script.sh"
        case "node":
            return "script.js"
        case _:
            return "script.txt"
