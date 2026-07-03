#!/usr/bin/env python3
"""Summarize CapDepMac Swift source coverage from SwiftPM's llvm JSON export."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COVERAGE = (
    ROOT
    / "apps/macos/CapDep/.build/arm64-apple-macosx/debug/codecov/CapDepMac.json"
)
DEFAULT_SOURCE_ROOT = ROOT / "apps/macos/CapDep/Sources"


@dataclass(frozen=True)
class SwiftCoverageRow:
    file: str
    covered_lines: int
    lines: int
    covered_functions: int
    functions: int
    covered_regions: int
    regions: int

    @property
    def line_percent(self) -> float:
        return 100.0 if self.lines == 0 else self.covered_lines / self.lines * 100.0

    @property
    def function_percent(self) -> float:
        return 100.0 if self.functions == 0 else self.covered_functions / self.functions * 100.0

    @property
    def region_percent(self) -> float:
        return 100.0 if self.regions == 0 else self.covered_regions / self.regions * 100.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "line_percent": round(self.line_percent, 2),
            "covered_lines": self.covered_lines,
            "lines": self.lines,
            "function_percent": round(self.function_percent, 2),
            "covered_functions": self.covered_functions,
            "functions": self.functions,
            "region_percent": round(self.region_percent, 2),
            "covered_regions": self.covered_regions,
            "regions": self.regions,
        }


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(
            f"Swift coverage JSON not found: {path}\n"
            "Run `swift test --enable-code-coverage` in apps/macos/CapDep first.",
        ) from None
    if not data.get("data"):
        raise SystemExit(f"Swift coverage JSON has no data entries: {path}")
    return data


def collect_rows(coverage: dict[str, Any], source_root: Path) -> list[SwiftCoverageRow]:
    source_prefix = str(source_root.resolve()) + "/"
    rows: list[SwiftCoverageRow] = []
    for file_data in coverage["data"][0].get("files", []):
        filename = str(file_data.get("filename") or "")
        if not filename.startswith(source_prefix):
            continue
        summary = file_data.get("summary") or {}
        lines = summary.get("lines") or {}
        functions = summary.get("functions") or {}
        regions = summary.get("regions") or {}
        rows.append(
            SwiftCoverageRow(
                file=filename[len(source_prefix) :],
                covered_lines=int(lines.get("covered", 0)),
                lines=int(lines.get("count", 0)),
                covered_functions=int(functions.get("covered", 0)),
                functions=int(functions.get("count", 0)),
                covered_regions=int(regions.get("covered", 0)),
                regions=int(regions.get("count", 0)),
            )
        )
    return sorted(rows, key=lambda row: (row.line_percent, row.file))


def totals(rows: list[SwiftCoverageRow]) -> SwiftCoverageRow:
    return SwiftCoverageRow(
        file="TOTAL",
        covered_lines=sum(row.covered_lines for row in rows),
        lines=sum(row.lines for row in rows),
        covered_functions=sum(row.covered_functions for row in rows),
        functions=sum(row.functions for row in rows),
        covered_regions=sum(row.covered_regions for row in rows),
        regions=sum(row.regions for row in rows),
    )


def _format(rows: list[SwiftCoverageRow], *, limit: int | None, fail_under: float) -> str:
    total = totals(rows)
    shown = rows[:limit] if limit is not None else rows
    lines = [
        f"Swift source files: {len(rows)}",
        (
            f"TOTAL lines {total.covered_lines}/{total.lines} "
            f"{total.line_percent:.2f}% | functions "
            f"{total.covered_functions}/{total.functions} {total.function_percent:.2f}% | "
            f"regions {total.covered_regions}/{total.regions} {total.region_percent:.2f}%"
        ),
        "",
        f"{'lines':>9} {'functions':>13} {'regions':>13} file",
        "-" * 72,
    ]
    for row in shown:
        marker = "!" if row.line_percent < fail_under else " "
        lines.append(
            f"{row.line_percent:7.2f}%{marker} "
            f"{row.covered_lines:5}/{row.lines:<5} "
            f"{row.function_percent:6.2f}% {row.covered_functions:4}/{row.functions:<4} "
            f"{row.region_percent:6.2f}% {row.covered_regions:4}/{row.regions:<4} "
            f"{row.file}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage", type=Path, default=DEFAULT_COVERAGE)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--fail-under", type=float, default=0.0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)

    rows = collect_rows(_load_json(args.coverage), args.source_root)
    if not rows:
        raise SystemExit(f"No Swift source files found under {args.source_root}")

    total = totals(rows)
    if args.json_output:
        print(
            json.dumps(
                {"total": total.to_dict(), "files": [row.to_dict() for row in rows]},
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(_format(rows, limit=args.limit, fail_under=args.fail_under))

    if total.line_percent + 1e-9 < args.fail_under:
        print(
            f"Swift source line coverage {total.line_percent:.2f}% below "
            f"{args.fail_under:.0f}%",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
