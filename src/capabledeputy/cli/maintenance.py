"""`capdep maintenance` — manual cleanup of devbox containers + workspaces.

Subcommands:

    capdep maintenance status
        Show a summary of what's currently using disk: live + stopped
        capdep-devbox containers (via `podman ps -a --filter name=`),
        per-session workspace dirs with sizes + age, audit log size.

    capdep maintenance containers [--apply]
        Without --apply: list every `capdep-devbox-*` container the
        local podman knows about. With --apply: `podman rm -f` every
        STOPPED one (the auto-reaper handles idle running ones).
        Operators who want to nuke even running devboxes use
        `podman ps --format {{.Names}} | grep capdep-devbox | xargs podman rm -f`
        directly — that's a foot-cannon we keep behind manual escalation.

    capdep maintenance workspaces [--apply]
        Walk `$XDG_STATE_HOME/capdep/devbox/`. List every per-session
        subdir with its disk usage. With --apply: delete the dirs for
        sessions that no longer exist in the state DB AND have no
        currently-running container.

Both `containers` and `workspaces` default to dry-run so a fresh run
is always safe — `--apply` is the deliberate escalation.

Audit-log rotation is intentionally NOT included here yet — it's a
separate maintenance concern with rotation/archive considerations.
Use `> /dev/null` redirection at the OS level for now.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from capabledeputy.ipc.client import DaemonClient, DaemonNotRunningError
from capabledeputy.ipc.socket_path import default_socket_path
from capabledeputy.paths import default_audit_log_path
from capabledeputy.substrate.podman_devbox import _default_workspace_root

maintenance_app = typer.Typer(
    help="Clean up devbox containers and per-session workspace dirs.",
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)


# --- shared helpers ------------------------------------------------------


def _format_bytes(n: int) -> str:
    """Compact size: 12345 → '12.1 KiB'. Matches what `du -h` would
    show without depending on the OS coreutils variant."""
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    size = float(n)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:,.1f} {unit}" if unit != "B" else f"{int(size):,} B"
        size /= 1024
    return f"{n} B"  # unreachable


def _dir_size(path: Path) -> int:
    """Recursive byte count for `path`. Symlinks counted by their
    target size (matches `du -L`); errors swallow to 0 so a
    permission-denied entry doesn't break the whole scan."""
    total = 0
    if not path.exists():
        return 0
    for entry in path.rglob("*"):
        try:
            if entry.is_file():
                total += entry.stat().st_size
        except OSError:
            continue
    return total


def _list_capdep_containers() -> list[dict[str, str]]:
    """Ask podman for every container named `capdep-devbox-*`. Returns
    a list of dicts with keys: name, status, image, created. Empty if
    podman is missing or returns nothing (treated as "nothing to do")."""
    podman = shutil.which("podman") or "podman"
    try:
        result = subprocess.run(
            [
                podman,
                "ps",
                "-a",
                "--filter",
                "name=capdep-devbox-",
                "--format",
                "{{.Names}}\t{{.Status}}\t{{.Image}}\t{{.CreatedAt}}",
            ],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    out: list[dict[str, str]] = []
    for line in result.stdout.decode("utf-8", errors="replace").splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        out.append(
            {
                "name": parts[0],
                "status": parts[1],
                "image": parts[2],
                "created": parts[3],
            },
        )
    return out


def _live_session_ids() -> set[str]:
    """Set of session ids currently known to the daemon. Empty set
    when the daemon isn't running — falls through to a fail-safe
    "treat everything as orphan" decision the operator can override."""
    try:
        client = DaemonClient(default_socket_path())
        result = client.call("session.list")
        return {s["id"] for s in result.get("sessions", [])}
    except (DaemonNotRunningError, Exception):
        return set()


def _force_remove(container_name: str) -> bool:
    podman = shutil.which("podman") or "podman"
    try:
        result = subprocess.run(
            [podman, "rm", "-f", container_name],
            capture_output=True,
            timeout=15,
            check=False,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# --- status --------------------------------------------------------------


@maintenance_app.command("status")
def status_command() -> None:
    """One-shot summary: containers, workspaces, audit log size.

    Read-only — nothing is deleted. Use this before deciding whether
    to run `containers --apply` or `workspaces --apply`."""
    # Containers
    containers = _list_capdep_containers()
    running = [c for c in containers if c["status"].lower().startswith("up")]
    stopped = [c for c in containers if not c["status"].lower().startswith("up")]
    console.print(
        f"[bold]containers[/bold]: {len(containers)} total "
        f"([green]{len(running)} running[/green], "
        f"[dim]{len(stopped)} stopped[/dim])",
    )
    if containers:
        console.print(
            "  [dim]run `capdep maintenance containers` for the full list[/dim]",
        )

    # Workspaces
    root = _default_workspace_root()
    if root.is_dir():
        session_dirs = [p for p in root.iterdir() if p.is_dir()]
        total = sum(_dir_size(p) for p in session_dirs)
        console.print(
            f"[bold]workspaces[/bold]: {len(session_dirs)} session dir(s) "
            f"at {root} — total {_format_bytes(total)}",
        )
    else:
        console.print(
            f"[bold]workspaces[/bold]: no devbox state dir at {root}",
        )

    # Audit log
    audit = default_audit_log_path()
    if audit.is_file():
        size = audit.stat().st_size
        console.print(
            f"[bold]audit log[/bold]: {_format_bytes(size)} at {audit}",
        )
    else:
        console.print(
            f"[bold]audit log[/bold]: no log file yet at {audit}",
        )


# --- containers ----------------------------------------------------------


@maintenance_app.command("containers")
def containers_command(
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help=(
                "Actually remove stopped capdep-devbox-* containers. "
                "Default is dry-run (list only)."
            ),
        ),
    ] = False,
) -> None:
    """List `capdep-devbox-*` containers; with --apply, remove stopped ones.

    Running containers are managed by the daemon's idle reaper —
    this command never tears them down. To force-kill a runaway
    container, use `capdep chat` → `devbox.stop` (in-session) or
    `podman rm -f <name>` (out-of-band)."""
    containers = _list_capdep_containers()
    if not containers:
        console.print("[dim]no capdep-devbox-* containers found[/dim]")
        return

    table = Table(title=f"capdep-devbox containers ({len(containers)})")
    table.add_column("Name", overflow="fold")
    table.add_column("Status")
    table.add_column("Image")
    table.add_column("Created")
    for c in containers:
        table.add_row(c["name"], c["status"], c["image"], c["created"])
    console.print(table)

    stopped = [c for c in containers if not c["status"].lower().startswith("up")]
    if not stopped:
        console.print(
            "\n[green]all containers running[/green] — nothing for --apply to remove",
        )
        return

    if not apply:
        console.print(
            f"\n[yellow]{len(stopped)} stopped container(s) eligible for "
            f"removal.[/yellow] Re-run with [bold]--apply[/bold] to remove.",
        )
        return

    n_removed = 0
    for c in stopped:
        if _force_remove(c["name"]):
            console.print(f"  removed [dim]{c['name']}[/dim]")
            n_removed += 1
        else:
            err_console.print(
                f"  [red]failed to remove[/red] {c['name']}",
            )
    console.print(
        f"\n[green]removed {n_removed}/{len(stopped)} stopped container(s).[/green]",
    )


# --- workspaces ----------------------------------------------------------


@maintenance_app.command("workspaces")
def workspaces_command(
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help=(
                "Actually delete workspace dirs for sessions that no "
                "longer exist. Default is dry-run."
            ),
        ),
    ] = False,
) -> None:
    """Walk devbox workspace state. Without --apply: list size + age
    per session dir. With --apply: delete dirs for sessions that no
    longer appear in the live state DB AND have no running container.

    Safety: a session dir is only deleted when BOTH conditions hold —
    daemon doesn't know the session AND no container with the
    session id in its name is running. This prevents nuking work
    in-flight just because the daemon happens to be down."""
    root = _default_workspace_root()
    if not root.is_dir():
        console.print(f"[dim]no devbox state dir at {root}[/dim]")
        return

    session_dirs = sorted([p for p in root.iterdir() if p.is_dir()])
    if not session_dirs:
        console.print(f"[dim]{root} is empty[/dim]")
        return

    live_sessions = _live_session_ids()
    containers = _list_capdep_containers()
    running_session_ids: set[str] = set()
    for c in containers:
        if not c["status"].lower().startswith("up"):
            continue
        name = c["name"]
        if name.startswith("capdep-devbox-"):
            # capdep-devbox-<UUID>-<spec_id>
            tail = name[len("capdep-devbox-") :]
            # UUID4 is 8-4-4-4-12 hex digits. First 5 dash-separated
            # tokens form the UUID; the 6th onward is the spec_id.
            tokens = tail.split("-")
            if len(tokens) >= 5:
                uuid_str = "-".join(tokens[:5])
                running_session_ids.add(uuid_str)

    table = Table(title=f"workspaces at {root}")
    table.add_column("Session", overflow="fold")
    table.add_column("Size", justify="right")
    table.add_column("State")
    table.add_column("Specs")

    orphans: list[Path] = []
    total_orphan_bytes = 0
    for sdir in session_dirs:
        sid = sdir.name
        size = _dir_size(sdir)
        spec_dirs = sorted([p.name for p in sdir.iterdir() if p.is_dir()])
        is_live_session = sid in live_sessions
        is_running = sid in running_session_ids
        if is_live_session and is_running:
            state = "[green]session + container live[/green]"
        elif is_live_session:
            state = "[yellow]session live, no container[/yellow]"
        elif is_running:
            state = "[yellow]container live, session unknown[/yellow]"
        else:
            state = "[red]orphan (eligible for removal)[/red]"
            orphans.append(sdir)
            total_orphan_bytes += size
        table.add_row(
            sid[:13] + "…" if len(sid) > 13 else sid,
            _format_bytes(size),
            state,
            ", ".join(spec_dirs) or "-",
        )
    console.print(table)

    if not orphans:
        console.print(
            "\n[green]no orphan workspace dirs[/green] — nothing for --apply to remove",
        )
        return

    summary = (
        f"\n[yellow]{len(orphans)} orphan dir(s) "
        f"({_format_bytes(total_orphan_bytes)}) eligible for removal."
        "[/yellow]"
    )
    if not apply:
        console.print(summary)
        console.print(
            "  Re-run with [bold]--apply[/bold] to delete.",
        )
        return

    console.print(summary)
    n_removed = 0
    bytes_freed = 0
    for p in orphans:
        size = _dir_size(p)
        try:
            shutil.rmtree(p)
            console.print(f"  removed [dim]{p}[/dim]")
            n_removed += 1
            bytes_freed += size
        except OSError as e:
            err_console.print(f"  [red]failed[/red] {p}: {e}")
    console.print(
        f"\n[green]removed {n_removed}/{len(orphans)} dir(s); "
        f"freed {_format_bytes(bytes_freed)}.[/green]",
    )


def _exit_with(message: str, code: int = 1) -> None:
    err_console.print(f"[red]error:[/red] {message}")
    sys.exit(code)
