from __future__ import annotations

from capabledeputy.cli import session as session_cli
from capabledeputy.cli import tool as tool_cli


def test_render_session_accepts_current_structured_daemon_schema(monkeypatch) -> None:
    printed: list[str] = []
    monkeypatch.setattr(session_cli.console, "print", printed.append)

    session_cli._render_session(
        {
            "id": "session-id",
            "status": "active",
            "created_at": "now",
            "updated_at": "now",
            "label_state": {"a": [], "b": []},
            "capability_set": [{"kind": "IMAP_READ", "pattern": "*"}],
        },
    )

    assert any("IMAP_READ(*)" in line for line in printed)


def test_tool_show_accepts_current_metadata_without_legacy_labels(monkeypatch) -> None:
    printed: list[str] = []
    monkeypatch.setattr(tool_cli, "_call", lambda *_: {"name": "workspace.read"})
    monkeypatch.setattr(tool_cli.console, "print", printed.append)

    tool_cli.tool_show("workspace.read")

    assert printed[0] == "[bold]workspace.read[/bold]"
