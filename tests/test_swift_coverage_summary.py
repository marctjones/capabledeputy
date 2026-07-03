from __future__ import annotations

from pathlib import Path

from scripts.swift_coverage_summary import collect_rows, totals


def _file(path: str, covered: int, count: int, functions: tuple[int, int]) -> dict:
    return {
        "filename": path,
        "summary": {
            "lines": {"covered": covered, "count": count, "percent": 0},
            "functions": {
                "covered": functions[0],
                "count": functions[1],
                "percent": 0,
            },
            "regions": {"covered": covered + 1, "count": count + 1, "percent": 0},
        },
    }


def test_collect_rows_filters_to_swift_source_root(tmp_path: Path) -> None:
    source = tmp_path / "Sources"
    source.mkdir()
    coverage = {
        "data": [
            {
                "files": [
                    _file(str(source / "ChatView.swift"), 3, 10, (1, 5)),
                    _file(str(source / "Models.swift"), 8, 10, (4, 5)),
                    _file(str(tmp_path / "Tests/ModelsTests.swift"), 10, 10, (1, 1)),
                ]
            }
        ]
    }

    rows = collect_rows(coverage, source)
    total = totals(rows)

    assert [row.file for row in rows] == ["ChatView.swift", "Models.swift"]
    assert round(total.line_percent, 2) == 55.0
    assert round(total.function_percent, 2) == 50.0
