"""003 audit subcommands: storage-shape, etc.

T004 registered the subcommand surface; T016 wired in the real shape
check via policy/storage_audit.py. The CLI itself is a thin wrapper
around audit_storage_shape() that pretty-prints the report and exits
non-zero on any FR-045/SC-019 violation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from capabledeputy.paths import default_state_db_path
from capabledeputy.policy.storage_audit import audit_storage_shape

console = Console()
err_console = Console(stderr=True)


def storage_shape_command(
    db: Annotated[
        Path | None,
        typer.Option(
            "--db",
            help=(
                "Path to the session store db. Defaults to CAPDEP_STATE_DB "
                "env or the per-user data dir."
            ),
        ),
    ] = None,
) -> None:
    """Audit that every sessions row populates the v0.9 four-axis fields
    (FR-045, SC-019). Exits non-zero on any shape violation."""
    db_path = db or default_state_db_path()
    console.print(f"[dim]db: {db_path}[/dim]")
    report = audit_storage_shape(db_path)
    if report.n_total == 0:
        console.print("[yellow]no sessions stored; nothing to audit[/yellow]")
        raise typer.Exit(code=0)
    if report.ok:
        console.print(
            f"[green]ok[/green] — {report.n_total} session(s) pass the "
            "four-axis storage shape check (FR-045 / SC-019).",
        )
        raise typer.Exit(code=0)
    err_console.print(
        f"[red]FAIL[/red] — {len(report.bad_rows)}/{report.n_total} sessions "
        "violate the four-axis storage shape:",
    )
    for sid, reason in report.bad_rows[:20]:
        err_console.print(f"  - {sid}: {reason}")
    if len(report.bad_rows) > 20:
        err_console.print(f"  ... and {len(report.bad_rows) - 20} more")
    raise typer.Exit(code=1)
