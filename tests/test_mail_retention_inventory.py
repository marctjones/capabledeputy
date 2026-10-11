import importlib.util
import json
import sqlite3
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "retention", Path(__file__).parents[1] / "scripts/inspect-mail-retention.py"
)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_inventory_counts_legacy_content_without_rendering_or_modifying_it(tmp_path):
    secret = "PRIVATE-CONTENT"
    audit = tmp_path / "audit.jsonl"
    audit.write_text(
        json.dumps({"event_type": "tool.returned", "payload": {"output": secret}})
        + "\n"
        + json.dumps({"event_type": "tool.returned", "payload": {"output_redacted": True}})
        + "\n"
    )
    audit.chmod(0o600)
    before = audit.read_bytes()
    with sqlite3.connect(tmp_path / "state.db") as con:
        con.execute("create table email_sources(data text)")
        con.execute("insert into email_sources values (?)", (secret,))
    result = module.inventory(tmp_path)
    assert result["legacy_raw_tool_returns"] == 1
    assert result["tool_return_commitments"] == 1
    assert result["table_counts"]["email_sources"] == 1
    assert secret not in json.dumps(result)
    assert audit.read_bytes() == before
    assert next(r for r in result["files"] if r["kind"] == "audit.jsonl")["owner_only"]
