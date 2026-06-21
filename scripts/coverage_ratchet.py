#!/usr/bin/env python3
"""Per-surface coverage ratchet.

This intentionally avoids one repo-wide percentage. CapDep has distinct safety
surfaces, so coverage is tracked independently for daemon code, clients, MCP
surfaces, bundled MCP servers, and tool implementations. The checked-in
baseline is a floor: `--update` can only raise entries unless
`--allow-decrease` is supplied for an explicit reset.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COVERAGE = ROOT / "coverage.json"
DEFAULT_BASELINE = ROOT / "coverage-ratchet.json"


STATIC_GROUPS: dict[str, tuple[str, ...]] = {
    "daemon.all": ("src/capabledeputy/daemon/*.py",),
    "clients.cli": ("src/capabledeputy/cli/*.py",),
    "clients.tui": ("src/capabledeputy/tui/**/*.py",),
    "clients.mcp_control": ("src/capabledeputy/mcp_server/control.py",),
    "mcp.session_server": ("src/capabledeputy/mcp_server/server.py",),
    "mcp.admin_server": ("src/capabledeputy/mcp_server/admin.py",),
    "mcp.resources_prompts": (
        "src/capabledeputy/mcp_server/resources.py",
        "src/capabledeputy/mcp_server/prompts.py",
    ),
    "tools.registry_dispatch": (
        "src/capabledeputy/tools/client.py",
        "src/capabledeputy/tools/registry.py",
        "src/capabledeputy/tools/descriptors.py",
        "src/capabledeputy/tools/source_flow.py",
        "src/capabledeputy/tools/policy_hooks.py",
    ),
    "tools.native_all": ("src/capabledeputy/tools/native/*.py",),
}


@dataclass(frozen=True)
class Metric:
    name: str
    percent: float
    covered: int
    statements: int
    files: tuple[str, ...]


def _normalize(path: str) -> str:
    return path.replace("\\", "/")


def _load_coverage(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(
            f"coverage file not found: {path}\n"
            "Run pytest with --cov-report=json:coverage.json first.",
        ) from None
    files = raw.get("files")
    if not isinstance(files, dict):
        raise SystemExit(f"coverage file has no files object: {path}")
    return raw


def _file_stats(files: dict[str, Any], file_name: str) -> tuple[int, int]:
    summary = files[file_name].get("summary", {})
    statements = int(summary.get("num_statements", 0))
    covered = int(summary.get("covered_lines", 0))
    return covered, statements


def _metric_for_patterns(
    files: dict[str, Any],
    name: str,
    patterns: tuple[str, ...],
) -> Metric | None:
    matched = tuple(
        sorted(
            f for f in files if any(fnmatch.fnmatch(_normalize(f), pattern) for pattern in patterns)
        )
    )
    if not matched:
        return None
    covered = 0
    statements = 0
    for file_name in matched:
        file_covered, file_statements = _file_stats(files, file_name)
        covered += file_covered
        statements += file_statements
    percent = 100.0 if statements == 0 else (covered / statements) * 100.0
    return Metric(name=name, percent=percent, covered=covered, statements=statements, files=matched)


def _auto_file_groups(
    files: dict[str, Any], prefix: str, directory: str
) -> dict[str, tuple[str, ...]]:
    groups: dict[str, tuple[str, ...]] = {}
    normalized_directory = directory.rstrip("/") + "/"
    for file_name in sorted(files):
        normalized = _normalize(file_name)
        if not normalized.startswith(normalized_directory):
            continue
        path = Path(normalized)
        if path.name == "__init__.py":
            continue
        if path.parent.as_posix() != normalized_directory.rstrip("/"):
            continue
        stem = path.stem.strip("_").replace("_", "-")
        groups[f"{prefix}.{stem}"] = (normalized,)
    return groups


def collect_metrics(coverage: dict[str, Any]) -> dict[str, Metric]:
    files = {_normalize(name): data for name, data in coverage["files"].items()}
    group_patterns: dict[str, tuple[str, ...]] = dict(STATIC_GROUPS)
    group_patterns.update(
        _auto_file_groups(files, "daemon.file", "src/capabledeputy/daemon"),
    )
    group_patterns.update(
        _auto_file_groups(files, "mcp_server.bundled", "src/capabledeputy/mcp_servers"),
    )
    group_patterns.update(
        _auto_file_groups(files, "tools.native", "src/capabledeputy/tools/native"),
    )

    metrics: dict[str, Metric] = {}
    for name, patterns in sorted(group_patterns.items()):
        metric = _metric_for_patterns(files, name, patterns)
        if metric is not None:
            metrics[name] = metric
    return metrics


def _load_baseline(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema": 1,
            "targets": {"near_term": 85.0, "stretch": 90.0},
            "groups": {},
        }
    return json.loads(path.read_text(encoding="utf-8"))


def _entry(metric: Metric) -> dict[str, Any]:
    return {
        "min_percent": round(metric.percent, 2),
        "covered": metric.covered,
        "statements": metric.statements,
        "files": list(metric.files),
    }


def update_baseline(
    baseline_path: Path,
    metrics: dict[str, Metric],
    *,
    allow_decrease: bool,
) -> int:
    baseline = _load_baseline(baseline_path)
    existing_groups = dict(baseline.get("groups") or {})
    groups = {name: entry for name, entry in existing_groups.items() if name in metrics}
    failures: list[str] = []
    for name, metric in metrics.items():
        current = _entry(metric)
        previous = groups.get(name)
        if previous is not None:
            old_floor = float(previous.get("min_percent", 0.0))
            if current["min_percent"] < old_floor and not allow_decrease:
                failures.append(
                    f"{name}: current {current['min_percent']:.2f}% "
                    f"is below existing floor {old_floor:.2f}%",
                )
                continue
            if current["min_percent"] < old_floor:
                current["previous_min_percent"] = old_floor
            else:
                current["min_percent"] = max(current["min_percent"], old_floor)
        groups[name] = current

    if failures:
        for failure in failures:
            print(f"coverage ratchet update refused: {failure}", file=sys.stderr)
        return 1

    baseline["schema"] = 1
    baseline.setdefault("targets", {"near_term": 85.0, "stretch": 90.0})
    baseline["groups"] = {name: groups[name] for name in sorted(groups)}
    baseline_path.write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n")
    print(f"updated {baseline_path} with {len(metrics)} coverage groups")
    return 0


def check_baseline(baseline_path: Path, metrics: dict[str, Metric]) -> int:
    baseline = _load_baseline(baseline_path)
    groups = baseline.get("groups") or {}
    failures: list[str] = []

    missing = sorted(set(metrics) - set(groups))
    if missing:
        failures.append(
            "new coverage groups missing from baseline: "
            + ", ".join(missing)
            + " (run scripts/coverage_ratchet.py --update)",
        )

    for name, entry in sorted(groups.items()):
        metric = metrics.get(name)
        if metric is None:
            failures.append(f"{name}: baseline group no longer matches any covered files")
            continue
        floor = float(entry.get("min_percent", 0.0))
        current_percent = round(metric.percent, 2)
        if current_percent + 1e-9 < floor:
            failures.append(f"{name}: {current_percent:.2f}% below floor {floor:.2f}%")

    if failures:
        for failure in failures:
            print(f"coverage ratchet failed: {failure}", file=sys.stderr)
        return 1

    target = float((baseline.get("targets") or {}).get("near_term", 85.0))
    below_target = [name for name, metric in sorted(metrics.items()) if metric.percent < target]
    print(
        f"coverage ratchet passed for {len(groups)} groups; "
        f"{len(below_target)} groups remain below {target:.0f}% target",
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage", type=Path, default=DEFAULT_COVERAGE)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--allow-decrease", action="store_true")
    args = parser.parse_args(argv)

    coverage = _load_coverage(args.coverage)
    metrics = collect_metrics(coverage)
    if args.update:
        return update_baseline(args.baseline, metrics, allow_decrease=args.allow_decrease)
    return check_baseline(args.baseline, metrics)


if __name__ == "__main__":
    raise SystemExit(main())
