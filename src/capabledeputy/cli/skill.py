"""Inspect imported SKILL.md packages."""

from __future__ import annotations

import json
from typing import Annotated, Any

import anyio
import typer
from rich.console import Console
from rich.table import Table

from capabledeputy.ipc.client import DaemonClient
from capabledeputy.ipc.socket_path import default_socket_path

skill_app = typer.Typer(help="Inspect SKILL.md package imports.", no_args_is_help=True)
console = Console()


async def _call(method: str, params: dict[str, Any] | None = None) -> Any:
    return await DaemonClient(default_socket_path()).call(method, params or {})


def _call_or_exit(method: str, params: dict[str, Any] | None = None) -> Any:
    return anyio.run(lambda: _call(method, params))


@skill_app.command("list")
def list_skills(
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    result = _call_or_exit("skill.list", {})
    if json_output:
        console.print_json(data=result)
        return
    table = Table(title=f"Skills ({len(result.get('skills', []))})")
    table.add_column("Name")
    table.add_column("Mode")
    table.add_column("Tool")
    table.add_column("Guidance")
    table.add_column("Diagnostics")
    for skill in result.get("skills", []):
        table.add_row(
            str(skill.get("name", "")),
            str(skill.get("mode", "")),
            "yes" if skill.get("tool_enabled") else "no",
            "yes" if skill.get("guidance_enabled") else "no",
            "; ".join(str(item) for item in skill.get("diagnostics", [])),
        )
    console.print(table)


@skill_app.command("show")
def show_skill(
    name: str,
    include_body: Annotated[
        bool,
        typer.Option("--body", help="Include untrusted skill body."),
    ] = False,
) -> None:
    result = _call_or_exit("skill.show", {"name": name, "include_body": include_body})
    console.print_json(data=result)


@skill_app.command("diagnostics")
def diagnostics() -> None:
    result = _call_or_exit("skill.diagnostics", {})
    console.print_json(data=result)


@skill_app.command("guidance")
def guidance(
    name: str,
    session_id: Annotated[
        str | None,
        typer.Option("--session", help="Session id for audit attribution."),
    ] = None,
) -> None:
    result = _call_or_exit("skill.guidance", {"name": name, "session_id": session_id})
    console.print(json.dumps(result, indent=2, sort_keys=True))
