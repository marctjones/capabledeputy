"""CLI surface for daemon-owned safe scripting workflow artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import anyio
import typer
from rich.console import Console

from capabledeputy.ipc.client import DaemonClient
from capabledeputy.ipc.socket_path import default_socket_path

scripting_app = typer.Typer(help="Prepare safe scripting workflow artifacts.", no_args_is_help=True)
console = Console()
err_console = Console(stderr=True)


def _call(method: str, params: dict[str, Any]) -> dict[str, Any]:
    client = DaemonClient(default_socket_path())
    return dict(anyio.run(client.call, method, params))


@scripting_app.command("plan")
def scripting_plan(
    task: Annotated[str, typer.Argument(help="Practical scripting task to plan.")],
    workspace_root: Annotated[Path, typer.Option("--workspace-root", "-w")],
    workspace_id: Annotated[str, typer.Option("--workspace-id")] = "local",
    language: Annotated[str, typer.Option("--language")] = "python",
    target_path: Annotated[str, typer.Option("--target-path")] = "",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Ask the daemon for a safe scripting workflow plan."""
    params = {
        "task": task,
        "workspace_root": str(workspace_root),
        "workspace_id": workspace_id,
        "language": language,
    }
    if target_path:
        params["target_path"] = target_path
    _print_result("scripting.plan", params, json_output=json_output)


@scripting_app.command("prepare-script")
def scripting_prepare_script(
    code_path: Annotated[Path, typer.Argument(help="Generated script file to review.")],
    workspace_root: Annotated[Path, typer.Option("--workspace-root", "-w")],
    target_path: Annotated[str, typer.Option("--target-path")],
    workspace_id: Annotated[str, typer.Option("--workspace-id")] = "local",
    language: Annotated[str, typer.Option("--language")] = "python",
    title: Annotated[str, typer.Option("--title")] = "Generated script",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Prepare a reviewed SCRIPT artifact from exact script bytes."""
    params = {
        "workspace_root": str(workspace_root),
        "workspace_id": workspace_id,
        "target_path": target_path,
        "language": language,
        "title": title,
        "code": code_path.read_text(encoding="utf-8"),
    }
    _print_result("scripting.prepare_script", params, json_output=json_output)


@scripting_app.command("export-artifact")
def scripting_export_artifact(
    content_path: Annotated[Path, typer.Argument(help="Output file content to review.")],
    workspace_root: Annotated[Path, typer.Option("--workspace-root", "-w")],
    target_path: Annotated[str, typer.Option("--target-path")],
    workspace_id: Annotated[str, typer.Option("--workspace-id")] = "local",
    content_type: Annotated[str, typer.Option("--content-type")] = "text/plain",
    title: Annotated[str, typer.Option("--title")] = "Script output export",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Prepare a reviewed FILE_EXPORT artifact from exact output bytes."""
    params = {
        "workspace_root": str(workspace_root),
        "workspace_id": workspace_id,
        "target_path": target_path,
        "content_type": content_type,
        "title": title,
        "content": content_path.read_text(encoding="utf-8"),
    }
    _print_result("scripting.export_artifact", params, json_output=json_output)


@scripting_app.command("run-artifact")
def scripting_run_artifact(
    run_result_path: Annotated[Path, typer.Argument(help="Sandbox run result JSON to review.")],
    workspace_root: Annotated[Path, typer.Option("--workspace-root", "-w")],
    workspace_id: Annotated[str, typer.Option("--workspace-id")] = "local",
    title: Annotated[str, typer.Option("--title")] = "Sandbox script run",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Prepare a reviewed SCRIPT_RUN artifact from sandbox evidence."""
    try:
        run_result = json.loads(run_result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        err_console.print(f"[red]invalid run result JSON:[/red] {e}")
        raise typer.Exit(code=2) from e
    if not isinstance(run_result, dict):
        err_console.print("[red]invalid run result JSON:[/red] expected an object")
        raise typer.Exit(code=2)
    params = {
        "workspace_root": str(workspace_root),
        "workspace_id": workspace_id,
        "title": title,
        "run_result": run_result,
    }
    _print_result("scripting.run_artifact", params, json_output=json_output)


def _print_result(method: str, params: dict[str, Any], *, json_output: bool) -> None:
    try:
        result = _call(method, params)
    except Exception as e:
        err_console.print(f"[red]{method} failed:[/red] {e}")
        raise typer.Exit(code=1) from e
    if json_output:
        console.print_json(data=result)
        return
    review = result.get("review_artifact") or {}
    workflow = result.get("workflow") or {}
    if review:
        console.print(
            f"[green]{review.get('artifact_type')}[/green] "
            f"{review.get('title') or review.get('artifact_id')} "
            f"dest={review.get('destination_id')} hash={review.get('sha256')}",
        )
    elif workflow:
        console.print(
            f"[green]safe scripting plan[/green] "
            f"{workflow.get('language')} dest={workflow.get('script_destination_id')}",
        )
        for step in workflow.get("steps", []):
            console.print(f"  - {step.get('title')} ({step.get('artifact_type')})")
    else:
        console.print_json(data=result)
