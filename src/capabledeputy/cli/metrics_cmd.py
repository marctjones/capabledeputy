"""#323 — `capdep metrics`: dump the live in-process metrics snapshot."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

console = Console()


def metrics_command(
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
    otlp: Annotated[
        Path | None,
        typer.Option("--otlp", help="Write the snapshot as OTLP metrics JSON to this file."),
    ] = None,
) -> None:
    """Print the current metrics snapshot (counters, gauges, latency histograms).

    NB: metrics live in the process that served the turns — run inside the daemon
    process or a session that shares it; a fresh CLI process starts empty.

    `--otlp <file>` writes the snapshot in OTLP metrics JSON for a collector to
    pick up (file export — no network client is added to the daemon)."""
    from capabledeputy.observability import get_metrics, write_otlp_metrics

    snap = get_metrics().snapshot()
    if otlp is not None:
        write_otlp_metrics(snap, otlp, time_unix_nano=time.time_ns())
        console.print(f"[green]wrote OTLP metrics[/green] {otlp}")
        return
    if json_output:
        console.print_json(data=snap.as_dict())
        return
    if snap.counters:
        ct = Table("counter", "value", title="counters")
        for k, v in sorted(snap.counters.items()):
            ct.add_row(k, str(v))
        console.print(ct)
    if snap.gauges:
        gt = Table("gauge", "value", title="gauges (levels)")
        for k, gv in sorted(snap.gauges.items()):
            gt.add_row(k, f"{gv:g}")
        console.print(gt)
    if snap.histograms:
        ht = Table("histogram", "count", "p50", "p95", "p99", "max", title="latency (s)")
        for k, h in sorted(snap.histograms.items()):
            ht.add_row(
                k, str(h.count), f"{h.p50:.3f}", f"{h.p95:.3f}", f"{h.p99:.3f}", f"{h.max:.3f}"
            )
        console.print(ht)
    if not snap.counters and not snap.gauges and not snap.histograms:
        console.print("[dim]no metrics recorded in this process yet[/dim]")
