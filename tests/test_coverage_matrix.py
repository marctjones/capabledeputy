from __future__ import annotations

from scripts.coverage_matrix import collect_rows


def _coverage(files: dict[str, tuple[int, int, int, int]]) -> dict:
    return {
        "files": {
            path: {
                "summary": {
                    "covered_lines": covered,
                    "num_statements": statements,
                    "covered_branches": branch_covered,
                    "num_branches": branches,
                }
            }
            for path, (covered, statements, branch_covered, branches) in files.items()
        }
    }


def test_collect_rows_reports_package_and_module_coverage() -> None:
    packages, modules = collect_rows(
        _coverage(
            {
                "src/capabledeputy/daemon/session_handlers.py": (8, 10, 3, 5),
                "src/capabledeputy/daemon/__init__.py": (0, 0, 0, 0),
                "src/capabledeputy/cli/main.py": (4, 10, 1, 5),
                "tests/test_ignored.py": (1, 1, 0, 0),
            }
        )
    )

    by_package = {row.name: row for row in packages}
    by_module = {row.name: row for row in modules}

    assert by_package["cli"].percent == 40.0
    assert by_package["daemon"].percent == 80.0
    assert by_package["daemon"].branch_percent == 60.0
    assert by_module["daemon.session_handlers"].percent == 80.0
    assert "daemon.__init__" not in by_module
