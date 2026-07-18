"""#318 — `capdep service`: install a supervised, auto-restarting daemon service.

Generates + installs the per-platform unit from the spike-#314 design (launchd
`KeepAlive={SuccessfulExit:false}` on macOS, systemd `Restart=on-failure` on
Linux). The file write is deterministic; loading it (launchctl/systemctl) is
best-effort and always echoes the manual commands so a headless/locked-down box
can finish by hand.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

console = Console()
err_console = Console(stderr=True)

service_app = typer.Typer(
    help="Install/inspect the supervised daemon service.", no_args_is_help=True
)


@service_app.callback()
def _service_root() -> None:
    """Manage the supervised (auto-restarting) daemon service."""


def _platform() -> str:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return "unsupported"


def _unit_text_and_path(file: Path | None) -> tuple[str, Path, str]:
    from capabledeputy.service import (
        DEFAULT_LABEL,
        launchd_plist,
        launchd_plist_path,
        systemd_unit,
        systemd_unit_path,
    )

    plat = _platform()
    if plat == "macos":
        return launchd_plist(), file or launchd_plist_path(DEFAULT_LABEL), plat
    if plat == "linux":
        return systemd_unit(), file or systemd_unit_path(), plat
    raise typer.Exit(2)


@service_app.command("show")
def service_show(
    file: Annotated[Path | None, typer.Option("--file", help="Override the unit path.")] = None,
) -> None:
    """Print the supervised-service unit for this platform (no side effects)."""
    if _platform() == "unsupported":
        err_console.print(f"[red]unsupported platform[/red] {sys.platform!r}")
        raise typer.Exit(2)
    text, path, plat = _unit_text_and_path(file)
    console.print(f"[dim]# {plat} unit → {path}[/dim]")
    console.print(text)


@service_app.command("install")
def service_install(
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print, don't write.")] = False,
    load: Annotated[
        bool,
        typer.Option("--load", help="Also load the unit (launchctl/systemctl)."),
    ] = False,
    file: Annotated[Path | None, typer.Option("--file", help="Override the unit path.")] = None,
) -> None:
    """Write the supervised-service unit. Prints the load command; pass `--load`
    to also run it (otherwise review + load by hand)."""
    if _platform() == "unsupported":
        err_console.print(f"[red]unsupported platform[/red] {sys.platform!r}")
        raise typer.Exit(2)
    text, path, plat = _unit_text_and_path(file)
    if dry_run:
        console.print(f"[dim]# would write → {path}[/dim]")
        console.print(text)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    console.print(f"[green]wrote[/green] {path}")

    load_cmds = (
        [["launchctl", "bootstrap", f"gui/{_uid()}", str(path)]]
        if plat == "macos"
        else [
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", "--now", path.stem + ".service"],
        ]
    )
    if load:
        _best_effort_load(load_cmds)
    else:
        console.print("[dim]to load:[/dim]")
        for cmd in load_cmds:
            console.print(f"  {' '.join(cmd)}")


@service_app.command("uninstall")
def service_uninstall(
    file: Annotated[Path | None, typer.Option("--file", help="Override the unit path.")] = None,
) -> None:
    """Remove the supervised-service unit (best-effort unload first)."""
    if _platform() == "unsupported":
        raise typer.Exit(2)
    _, path, plat = _unit_text_and_path(file)
    if not path.is_file():
        console.print(f"[yellow]not installed[/yellow] — no unit at {path}")
        return
    unload = (
        [["launchctl", "bootout", f"gui/{_uid()}", str(path)]]
        if plat == "macos"
        else [["systemctl", "--user", "disable", "--now", path.stem + ".service"]]
    )
    _best_effort_load(unload)  # unloading a not-loaded unit is harmless
    path.unlink(missing_ok=True)
    console.print(f"[green]removed[/green] {path}")


@service_app.command("status")
def service_status(
    file: Annotated[Path | None, typer.Option("--file", help="Override the unit path.")] = None,
) -> None:
    """Report whether the supervised service is installed."""
    if _platform() == "unsupported":
        console.print(f"[yellow]unsupported platform[/yellow] {sys.platform!r}")
        raise typer.Exit(0)
    _, path, plat = _unit_text_and_path(file)
    if path.is_file():
        console.print(f"[green]installed[/green] ({plat}) → {path}")
    else:
        console.print(
            f"[yellow]not installed[/yellow] ({plat}) — run [bold]capdep service install[/bold]",
        )


def _uid() -> int:
    import os

    return os.getuid()


def _best_effort_load(cmds: list[list[str]]) -> None:
    for cmd in cmds:
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=15)
            console.print(f"[dim]ran: {' '.join(cmd)}[/dim]")
        except (OSError, subprocess.SubprocessError) as e:
            err_console.print(
                f"[yellow]could not run[/yellow] `{' '.join(cmd)}` ({e}); run it manually.",
            )
