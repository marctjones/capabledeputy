"""#323 — `capdep metrics`: dump the live in-process metrics snapshot."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

console = Console()


def metrics_command(
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Print the current metrics snapshot (counters + latency histograms).

    NB: metrics live in the process that served the turns — run inside the daemon
    process or a session that shares it; a fresh CLI process starts empty."""
    from capabledeputy.observability import get_metrics

    snap = get_metrics().snapshot()
    if json_output:
        console.print_json(data=snap.as_dict())
        return
    if snap.counters:
        ct = Table("counter", "value", title="counters")
        for k, v in sorted(snap.counters.items()):
            ct.add_row(k, str(v))
        console.print(ct)
    if snap.histograms:
        ht = Table("histogram", "count", "p50", "p95", "p99", "max", title="latency (s)")
        for k, h in sorted(snap.histograms.items()):
            ht.add_row(
                k, str(h.count), f"{h.p50:.3f}", f"{h.p95:.3f}", f"{h.p99:.3f}", f"{h.max:.3f}"
            )
        console.print(ht)
    if not snap.counters and not snap.histograms:
        console.print("[dim]no metrics recorded in this process yet[/dim]")
