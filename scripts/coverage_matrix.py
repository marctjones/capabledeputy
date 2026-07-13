#!/usr/bin/env python3
"""Package and module coverage matrix for CapDep.

This complements ``coverage_ratchet.py``. The ratchet enforces non-regression
for selected safety surfaces; this script makes every Python package/module
visible so low-coverage areas cannot hide behind a repo-wide average.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COVERAGE = ROOT / "coverage.json"
SOURCE_PREFIX = "src/capabledeputy/"

Scope = Literal["package", "module"]


@dataclass(frozen=True)
class CoverageRow:
    name: str
    scope: Scope
    covered: int
    statements: int
    branch_covered: int
    branches: int

    @property
    def percent(self) -> float:
        return 100.0 if self.statements == 0 else (self.covered / self.statements) * 100.0

    @property
    def branch_percent(self) -> float:
        return 100.0 if self.branches == 0 else (self.branch_covered / self.branches) * 100.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "scope": self.scope,
            "percent": round(self.percent, 2),
            "covered": self.covered,
            "statements": self.statements,
            "branch_percent": round(self.branch_percent, 2),
            "branch_covered": self.branch_covered,
            "branches": self.branches,
        }


def _load_coverage(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(
            f"coverage file not found: {path}\n"
            "Run pytest with --cov-report=json:coverage.json first.",
        ) from None
    if not isinstance(data.get("files"), dict):
        raise SystemExit(f"coverage file has no files object: {path}")
    return data


def _module_name(file_name: str) -> str | None:
    normalized = file_name.replace("\\", "/")
    if not normalized.startswith(SOURCE_PREFIX) or not normalized.endswith(".py"):
        return None
    rel = normalized[len(SOURCE_PREFIX) : -3]
    parts = rel.split("/")
    if parts[-1] == "__init__":
        return None
    return ".".join(parts)


def _package_name(module: str) -> str:
    return module.split(".", 1)[0] if "." in module else "(root)"


def collect_rows(coverage: dict[str, Any]) -> tuple[list[CoverageRow], list[CoverageRow]]:
    packages: dict[str, list[int]] = {}
    modules: list[CoverageRow] = []
    for file_name, file_data in coverage["files"].items():
        module = _module_name(file_name)
        if module is None:
            continue
        summary = file_data.get("summary", {})
        covered = int(summary.get("covered_lines", 0))
        statements = int(summary.get("num_statements", 0))
        branch_covered = int(summary.get("covered_branches", 0))
        branches = int(summary.get("num_branches", 0))
        modules.append(CoverageRow(module, "module", covered, statements, branch_covered, branches))
        bucket = packages.setdefault(_package_name(module), [0, 0, 0, 0])
        bucket[0] += covered
        bucket[1] += statements
        bucket[2] += branch_covered
        bucket[3] += branches

    package_rows = [
        CoverageRow(name, "package", covered, statements, branch_covered, branches)
        for name, (covered, statements, branch_covered, branches) in packages.items()
    ]
    package_rows.sort(key=lambda row: (row.percent, row.name))
    modules.sort(key=lambda row: (row.percent, row.name))
    return package_rows, modules


def _format_rows(rows: list[CoverageRow], *, fail_under: float, limit: int | None) -> str:
    shown = rows[:limit] if limit is not None else rows
    lines = [
        f"{'scope':8} {'coverage':>8} {'lines':>13} {'branches':>13} name",
        "-" * 64,
    ]
    for row in shown:
        marker = "!" if row.percent < fail_under else " "
        lines.append(
            f"{row.scope:8} {row.percent:7.2f}%{marker} "
            f"{row.covered:5}/{row.statements:<5} "
            f"{row.branch_percent:6.2f}% {row.branch_covered:4}/{row.branches:<4} "
            f"{row.name}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage", type=Path, default=DEFAULT_COVERAGE)
    parser.add_argument("--scope", choices=["package", "module", "all"], default="package")
    parser.add_argument("--fail-under", type=float, default=85.0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)

    package_rows, module_rows = collect_rows(_load_coverage(args.coverage))
    rows = {
        "package": package_rows,
        "module": module_rows,
        "all": package_rows + module_rows,
    }[args.scope]

    if args.json_output:
        print(json.dumps([row.to_dict() for row in rows], indent=2, sort_keys=True))
    else:
        print(_format_rows(rows, fail_under=args.fail_under, limit=args.limit))

    below = [row for row in rows if row.percent < args.fail_under]
    if below:
        print(
            f"{len(below)} {args.scope} coverage rows below {args.fail_under:.0f}%",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
