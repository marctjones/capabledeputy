"""003 audit subcommands: storage-shape, etc.

Phase 1 (T004) skeleton. The real storage-shape audit logic (verify
every sessions row populates the four axis fields per FR-045/SC-019)
lands in Foundational T016 — this file just registers the subcommand
and resolves the DB path so the surface exists for downstream code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from capabledeputy.paths import default_state_db_path

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
    (FR-045, SC-019). T004 skeleton — the real shape check lands in T016."""
    db_path = db or default_state_db_path()
    if not db_path.exists():
        err_console.print(f"[yellow]no session store at {db_path}; nothing to audit[/yellow]")
        raise typer.Exit(code=0)
    console.print(f"[dim]db: {db_path}[/dim]")
    console.print(
        "[yellow]storage-shape audit not yet implemented "
        "(T004 skeleton; real check lands in T016).[/yellow]",
    )
    raise typer.Exit(code=0)
