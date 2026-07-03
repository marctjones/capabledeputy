"""CLI surface for daemon-owned local image/model operations."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import anyio
import typer
from rich.console import Console
from rich.table import Table

from capabledeputy.ipc.client import DaemonClient, DaemonNotRunningError
from capabledeputy.ipc.socket_path import default_socket_path

image_app = typer.Typer(help="Inspect and run local image generation.", no_args_is_help=True)
console = Console()
err_console = Console(stderr=True)


async def _call(
    method: str,
    params: dict[str, Any],
    *,
    socket_path: str | None,
) -> dict[str, Any]:
    client = DaemonClient(default_socket_path() if socket_path is None else Path(socket_path))
    return dict(await client.call(method, params))


def _call_or_exit(
    method: str, params: dict[str, Any], *, socket_path: str | None
) -> dict[str, Any]:
    try:
        return anyio.run(lambda: _call(method, params, socket_path=socket_path))
    except DaemonNotRunningError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=2) from e
    except Exception as e:
        err_console.print(f"[red]{method} failed:[/red] {e}")
        raise typer.Exit(code=1) from e


@image_app.command("profiles")
def image_profiles_command(
    socket_path: Annotated[
        str | None,
        typer.Option("--socket", help="Override daemon socket path."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a table."),
    ] = False,
) -> None:
    """List daemon-known image generation profiles."""
    result = _call_or_exit("image.profiles", {}, socket_path=socket_path)
    if json_output:
        console.print_json(data=result)
        return
    selected = result.get("selected")
    table = Table("Profile", "Backend", "Model", "Steps", "Role", "Notes")
    for profile in result.get("profiles", []):
        marker = "*" if profile.get("id") == selected else " "
        role = str(profile.get("tier") or "")
        if profile.get("recommended"):
            role = f"{role} recommended".strip()
        if profile.get("slow"):
            role = f"{role} slow".strip()
        table.add_row(
            f"{marker} {profile.get('id')}",
            str(profile.get("backend") or ""),
            str(profile.get("model") or ""),
            str(profile.get("steps") or ""),
            role,
            str(profile.get("benchmark_note") or profile.get("description") or ""),
        )
    console.print(table)


@image_app.command("profile")
def image_profile_command(
    profile: Annotated[
        str | None,
        typer.Argument(help="Profile id to select. Omit to show the selected profile."),
    ] = None,
    socket_path: Annotated[
        str | None,
        typer.Option("--socket", help="Override daemon socket path."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a summary."),
    ] = False,
) -> None:
    """Show or update the daemon image profile."""
    method = "image.profile.set" if profile else "image.profile.get"
    params = {"profile": profile} if profile else {}
    result = _call_or_exit(method, params, socket_path=socket_path)
    if json_output:
        console.print_json(data=result)
        return
    _print_readiness(result.get("readiness") or {})
    changed = result.get("changed")
    suffix = f" changed={','.join(changed)}" if changed else ""
    console.print(f"[green]selected image profile:[/green] {result.get('selected')}{suffix}")


@image_app.command("readiness")
def image_readiness_command(
    profile: Annotated[
        str | None,
        typer.Option("--profile", help="Profile id to inspect."),
    ] = None,
    socket_path: Annotated[
        str | None,
        typer.Option("--socket", help="Override daemon socket path."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a summary."),
    ] = False,
) -> None:
    """Check model/account/backend readiness for local image generation."""
    params = {"profile": profile} if profile else {}
    result = _call_or_exit("image.readiness", params, socket_path=socket_path)
    if json_output:
        console.print_json(data=result)
        return
    _print_readiness(result)


@image_app.command("generate")
def image_generate_command(
    prompt: Annotated[str, typer.Argument(help="Prompt to generate.")],
    profile: Annotated[str | None, typer.Option("--profile")] = None,
    width: Annotated[int | None, typer.Option("--width")] = None,
    height: Annotated[int | None, typer.Option("--height")] = None,
    steps: Annotated[int | None, typer.Option("--steps")] = None,
    socket_path: Annotated[
        str | None,
        typer.Option("--socket", help="Override daemon socket path."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a summary."),
    ] = False,
) -> None:
    """Start a daemon-owned image generation job."""
    params: dict[str, Any] = {"prompt": prompt}
    for key, value in {
        "profile": profile,
        "width": width,
        "height": height,
        "steps": steps,
    }.items():
        if value is not None:
            params[key] = value
    result = _call_or_exit("image.jobs.start", params, socket_path=socket_path)
    if json_output:
        console.print_json(data=result)
        return
    job = result.get("job") or {}
    console.print(
        f"[green]image job queued:[/green] {job.get('id')} "
        f"profile={job.get('profile')} stream={job.get('stream')}"
    )


@image_app.command("jobs")
def image_jobs_command(
    limit: Annotated[int, typer.Option("--limit", help="Maximum jobs to show.")] = 20,
    socket_path: Annotated[
        str | None,
        typer.Option("--socket", help="Override daemon socket path."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a table."),
    ] = False,
) -> None:
    """List daemon image jobs."""
    result = _call_or_exit("image.jobs.list", {"limit": limit}, socket_path=socket_path)
    if json_output:
        console.print_json(data=result)
        return
    table = Table("Job", "Status", "Profile", "Backend", "Model", "Elapsed")
    for job in result.get("jobs", []):
        table.add_row(
            str(job.get("id") or ""),
            str(job.get("status") or ""),
            str(job.get("profile") or ""),
            str(job.get("backend") or ""),
            str(job.get("model") or ""),
            f"{float(job.get('elapsed_seconds') or 0):.1f}s",
        )
    console.print(table)


@image_app.command("job")
def image_job_command(
    job_id: Annotated[str, typer.Argument(help="Image job id.")],
    socket_path: Annotated[
        str | None,
        typer.Option("--socket", help="Override daemon socket path."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a summary."),
    ] = False,
) -> None:
    """Show a daemon image job."""
    result = _call_or_exit("image.jobs.get", {"job_id": job_id}, socket_path=socket_path)
    if json_output:
        console.print_json(data=result)
        return
    _print_job(result.get("job") or {})


@image_app.command("cancel")
def image_cancel_command(
    job_id: Annotated[str, typer.Argument(help="Image job id.")],
    socket_path: Annotated[
        str | None,
        typer.Option("--socket", help="Override daemon socket path."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a summary."),
    ] = False,
) -> None:
    """Request cancellation for a daemon image job."""
    result = _call_or_exit("image.jobs.cancel", {"job_id": job_id}, socket_path=socket_path)
    if json_output:
        console.print_json(data=result)
        return
    _print_job(result.get("job") or {})


def _print_readiness(readiness: dict[str, Any]) -> None:
    ok = "ok" if readiness.get("ok") else "not ready"
    console.print(
        f"[bold]profile[/bold]={readiness.get('profile')} "
        f"[bold]backend[/bold]={readiness.get('backend')} "
        f"[bold]model[/bold]={readiness.get('model')} "
        f"[bold]status[/bold]={ok}"
    )
    for check in readiness.get("checks", []):
        status = check.get("status")
        color = "green" if status == "ok" else "yellow" if status == "warning" else "red"
        console.print(f"  [{color}]{status}[/{color}] {check.get('id')}: {check.get('detail')}")
        if check.get("recovery") and status != "ok":
            console.print(f"    {check.get('recovery')}")


def _print_job(job: dict[str, Any]) -> None:
    console.print(
        f"[bold]{job.get('id')}[/bold] status={job.get('status')} "
        f"profile={job.get('profile')} backend={job.get('backend')} model={job.get('model')}"
    )
    if job.get("error"):
        console.print(f"[red]{job.get('error')}[/red]")
    result = job.get("result") or {}
    if result.get("image_path"):
        console.print(f"image: {result.get('image_path')}")
