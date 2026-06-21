import json
from pathlib import Path

from scripts.coverage_ratchet import check_baseline, collect_metrics, update_baseline


def _coverage(files: dict[str, tuple[int, int]]) -> dict:
    return {
        "files": {
            path: {
                "summary": {
                    "covered_lines": covered,
                    "num_statements": statements,
                }
            }
            for path, (covered, statements) in files.items()
        }
    }


def test_collect_metrics_tracks_independent_surfaces() -> None:
    metrics = collect_metrics(
        _coverage(
            {
                "src/capabledeputy/daemon/session_handlers.py": (8, 10),
                "src/capabledeputy/cli/main.py": (2, 10),
                "src/capabledeputy/mcp_server/control.py": (7, 10),
                "src/capabledeputy/mcp_servers/fetch.py": (5, 10),
                "src/capabledeputy/tools/native/memory.py": (6, 10),
            }
        )
    )

    assert metrics["daemon.all"].percent == 80.0
    assert metrics["daemon.file.session-handlers"].percent == 80.0
    assert metrics["clients.cli"].percent == 20.0
    assert metrics["clients.mcp_control"].percent == 70.0
    assert metrics["mcp_server.bundled.fetch"].percent == 50.0
    assert metrics["tools.native.memory"].percent == 60.0


def test_update_refuses_to_lower_existing_floor(tmp_path: Path) -> None:
    baseline = tmp_path / "coverage-ratchet.json"
    baseline.write_text(
        json.dumps(
            {
                "schema": 1,
                "groups": {
                    "clients.cli": {
                        "min_percent": 50.0,
                        "covered": 5,
                        "statements": 10,
                        "files": ["src/capabledeputy/cli/main.py"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    metrics = collect_metrics(_coverage({"src/capabledeputy/cli/main.py": (4, 10)}))

    assert update_baseline(baseline, metrics, allow_decrease=False) == 1


def test_check_fails_when_group_drops_below_floor(tmp_path: Path) -> None:
    baseline = tmp_path / "coverage-ratchet.json"
    baseline.write_text(
        json.dumps(
            {
                "schema": 1,
                "targets": {"near_term": 85.0, "stretch": 90.0},
                "groups": {
                    "daemon.all": {
                        "min_percent": 80.0,
                        "covered": 8,
                        "statements": 10,
                        "files": ["src/capabledeputy/daemon/session_handlers.py"],
                    },
                    "daemon.file.session-handlers": {
                        "min_percent": 80.0,
                        "covered": 8,
                        "statements": 10,
                        "files": ["src/capabledeputy/daemon/session_handlers.py"],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    metrics = collect_metrics(_coverage({"src/capabledeputy/daemon/session_handlers.py": (7, 10)}))

    assert check_baseline(baseline, metrics) == 1


def test_check_passes_at_floor(tmp_path: Path) -> None:
    baseline = tmp_path / "coverage-ratchet.json"
    baseline.write_text(
        json.dumps(
            {
                "schema": 1,
                "targets": {"near_term": 85.0, "stretch": 90.0},
                "groups": {
                    "daemon.all": {
                        "min_percent": 80.0,
                        "covered": 8,
                        "statements": 10,
                        "files": ["src/capabledeputy/daemon/session_handlers.py"],
                    },
                    "daemon.file.session-handlers": {
                        "min_percent": 80.0,
                        "covered": 8,
                        "statements": 10,
                        "files": ["src/capabledeputy/daemon/session_handlers.py"],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    metrics = collect_metrics(_coverage({"src/capabledeputy/daemon/session_handlers.py": (8, 10)}))

    assert check_baseline(baseline, metrics) == 0
