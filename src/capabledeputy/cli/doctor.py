"""#322 — `capdep doctor`: one end-to-end health check."""

from __future__ import annotations

import anyio
import typer
from rich.console import Console

console = Console()

_ICON = {"ok": "[green]✓[/green]", "warn": "[yellow]![/yellow]", "fail": "[red]✗[/red]"}


def doctor_command() -> None:
    """Run every health check (daemon liveness, config validity, state-DB
    integrity, LLM key) and print a summary. Exits non-zero if any check FAILS
    (warnings do not fail)."""
    from capabledeputy.diagnostics import overall_status, run_all

    checks = anyio.run(run_all)
    for c in checks:
        console.print(f"{_ICON.get(c.status, '?')} [bold]{c.name}[/bold]: {c.detail}")
    overall = overall_status(checks)
    if overall == "fail":
        console.print("\n[red]doctor: FAIL[/red] — one or more checks failed")
        raise typer.Exit(1)
    if overall == "warn":
        console.print("\n[yellow]doctor: OK with warnings[/yellow]")
        return
    console.print("\n[green]doctor: OK[/green]")
