"""Read-only local retention inventory. Never render stored payloads or paths."""

from __future__ import annotations

import json
import sqlite3
import stat
from pathlib import Path
from typing import Any

from capabledeputy.paths import default_data_dir

TABLES = ("sessions", "email_sources", "email_drafts", "email_operations", "request_records")


def inventory(root: Path) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": 1,
        "files": [],
        "legacy_raw_tool_returns": 0,
        "tool_return_commitments": 0,
        "invalid_audit_lines": 0,
        "retains_private_content": True,
        "automatic_deletion": False,
    }
    for name in ("state.db", "audit.jsonl", "chat-trace.jsonl"):
        path = root / name
        if not path.is_file():
            continue
        info = path.stat()
        report["files"].append(
            {
                "kind": name,
                "bytes": info.st_size,
                "owner_only": stat.S_IMODE(info.st_mode) & 0o077 == 0,
            }
        )
    audit = root / "audit.jsonl"
    if audit.is_file():
        for line in audit.open(encoding="utf-8"):
            try:
                event = json.loads(line)
                if event.get("event_type") == "tool.returned":
                    payload = event.get("payload", {})
                    report["legacy_raw_tool_returns"] += int("output" in payload)
                    report["tool_return_commitments"] += int(payload.get("output_redacted") is True)
            except (ValueError, TypeError, AttributeError):
                report["invalid_audit_lines"] += 1
    db = root / "state.db"
    if db.is_file():
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as connection:
            present = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            report["table_counts"] = {
                name: connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
                for name in TABLES
                if name in present
            }
    return report


if __name__ == "__main__":
    print(json.dumps(inventory(default_data_dir()), indent=2))
