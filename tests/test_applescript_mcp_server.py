from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from capabledeputy.mcp_servers import (
    apple_mail,
    applescript,
    keynote,
    macos,
    numbers,
    outlook,
    pages,
    powerpoint,
    word,
)


class FakeRunner:
    def __init__(self, result: applescript.AppleScriptResult | None = None) -> None:
        self.calls: list[tuple[str, list[str], float]] = []
        self.result = result or applescript.AppleScriptResult(
            stdout='{"ok":true}\n',
            stderr="",
            returncode=0,
        )

    async def __call__(
        self,
        script: str,
        argv: Sequence[str],
        timeout_seconds: float,
    ) -> applescript.AppleScriptResult:
        self.calls.append((script, list(argv), timeout_seconds))
        return self.result


def _write_catalog(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "catalog.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _tool_by_name(tools: list[Any], name: str) -> Any:
    for tool in tools:
        if tool.name == name:
            return tool
    raise KeyError(name)


def _tool_names(tools: list[Any]) -> set[str]:
    return {tool.name for tool in tools}


def test_load_catalog_exposes_only_declared_tools(tmp_path: Path) -> None:
    path = _write_catalog(
        tmp_path,
        """
schema_version: 1
tools:
  - name: mail.count_unread_inbox
    description: Count unread inbox messages.
    output_format: json
    input_schema:
      type: object
      properties: {}
      additionalProperties: false
    script: |
      on run argv
        return "{\\"unread_count\\": 0}"
      end run
""",
    )

    loaded_tools = applescript.tools(catalog_paths=[path], runner=FakeRunner())
    tool_names = {tool.name for tool in loaded_tools}

    assert tool_names == {"mail.count_unread_inbox"}
    assert "applescript.run" not in tool_names


@pytest.mark.asyncio
async def test_handler_passes_user_inputs_as_argv_not_script_text(tmp_path: Path) -> None:
    path = _write_catalog(
        tmp_path,
        """
schema_version: 1
tools:
  - name: mail.search_subject
    description: Search Mail subjects.
    output_format: json
    timeout_seconds: 7
    argv: [query, limit]
    input_schema:
      type: object
      properties:
        query: {type: string}
        limit: {type: integer, default: 10}
      required: [query]
      additionalProperties: false
    script: |
      on run argv
        set q to item 1 of argv
        return "{\\"matches\\":[]}"
      end run
""",
    )
    runner = FakeRunner(
        applescript.AppleScriptResult(stdout='{"matches":[]}', stderr="", returncode=0),
    )
    [tool] = applescript.tools(catalog_paths=[path], runner=runner)

    malicious_query = 'hello"; do shell script "touch /tmp/bad'
    result = await tool.handler({"query": malicious_query})

    assert isinstance(result, dict)
    assert result["result"] == {"matches": []}
    assert len(runner.calls) == 1
    script, argv, timeout = runner.calls[0]
    assert malicious_query not in script
    assert argv == [malicious_query, "10"]
    assert timeout == 7


@pytest.mark.asyncio
async def test_handler_rejects_missing_unknown_and_wrong_type_args(tmp_path: Path) -> None:
    path = _write_catalog(
        tmp_path,
        """
schema_version: 1
tools:
  - name: keynote.slide_title
    description: Read a slide title.
    argv: [slide_number]
    input_schema:
      type: object
      properties:
        slide_number: {type: integer}
      required: [slide_number]
      additionalProperties: false
    script: |
      on run argv
        return "ok"
      end run
""",
    )
    [tool] = applescript.tools(catalog_paths=[path], runner=FakeRunner())

    with pytest.raises(ValueError, match="missing required"):
        await tool.handler({})
    with pytest.raises(ValueError, match="unknown argument"):
        await tool.handler({"slide_number": 1, "extra": True})
    with pytest.raises(ValueError, match="must be integer"):
        await tool.handler({"slide_number": "1"})


@pytest.mark.asyncio
async def test_handler_parses_json_and_preserves_stderr(tmp_path: Path) -> None:
    path = _write_catalog(
        tmp_path,
        """
schema_version: 1
tools:
  - name: keynote.frontmost_document
    description: Read frontmost document metadata.
    output_format: json
    input_schema:
      type: object
      properties: {}
      additionalProperties: false
    script: |
      on run argv
        return "{\\"open\\":true}"
      end run
""",
    )
    runner = FakeRunner(
        applescript.AppleScriptResult(
            stdout='{"open":true,"slide_count":3}',
            stderr="diagnostic",
            returncode=0,
        ),
    )
    [tool] = applescript.tools(catalog_paths=[path], runner=runner)

    result = await tool.handler({})

    assert isinstance(result, dict)
    assert result["result"] == {"open": True, "slide_count": 3}
    assert result["stderr"] == "diagnostic"


@pytest.mark.asyncio
async def test_handler_raises_on_script_failure(tmp_path: Path) -> None:
    path = _write_catalog(
        tmp_path,
        """
schema_version: 1
tools:
  - name: mail.fail
    description: Fail.
    script: |
      on run argv
        error "nope"
      end run
""",
    )
    runner = FakeRunner(applescript.AppleScriptResult(stdout="", stderr="nope", returncode=1))
    [tool] = applescript.tools(catalog_paths=[path], runner=runner)

    with pytest.raises(RuntimeError, match="AppleScript failed"):
        await tool.handler({})


def test_catalog_validation_rejects_bad_argv_reference(tmp_path: Path) -> None:
    path = _write_catalog(
        tmp_path,
        """
schema_version: 1
tools:
  - name: bad.tool
    argv: [missing]
    input_schema:
      type: object
      properties: {}
      additionalProperties: false
    script: |
      on run argv
        return "ok"
      end run
""",
    )

    with pytest.raises(applescript.CatalogError, match="argv entry"):
        applescript.tools(catalog_paths=[path], runner=FakeRunner())


def test_catalog_paths_from_env_supports_multiple_paths(tmp_path: Path) -> None:
    first = tmp_path / "one.yaml"
    second = tmp_path / "two.yaml"
    env = {"CAPDEP_APPLESCRIPT_CATALOGS": f"{first}:{second}"}

    assert applescript.catalog_paths_from_env(env) == [first, second]


def test_specialized_apple_mail_server_exposes_bounded_tools() -> None:
    names = _tool_names(apple_mail.tools(runner=FakeRunner()))

    assert names == {
        "apple_mail.list_accounts",
        "apple_mail.list_mailboxes",
        "apple_mail.search_messages",
        "apple_mail.get_message",
        "apple_mail.create_draft",
    }
    assert "applescript.run" not in names


def test_specialized_keynote_server_exposes_bounded_tools() -> None:
    names = _tool_names(keynote.tools(runner=FakeRunner()))

    assert names == {
        "keynote.frontmost_document",
        "keynote.list_slides",
        "keynote.slide_text",
        "keynote.start_slideshow",
        "keynote.stop_slideshow",
    }
    assert "applescript.run" not in names


def test_specialized_pages_server_exposes_bounded_tools() -> None:
    names = _tool_names(pages.tools(runner=FakeRunner()))

    assert names == {
        "pages.frontmost_document",
        "pages.document_text",
        "pages.append_text",
        "pages.export_pdf",
    }
    assert "applescript.run" not in names


def test_specialized_numbers_server_exposes_bounded_tools() -> None:
    names = _tool_names(numbers.tools(runner=FakeRunner()))

    assert names == {
        "numbers.frontmost_document",
        "numbers.list_sheets",
        "numbers.table_summary",
        "numbers.get_cell_value",
        "numbers.set_cell_value",
        "numbers.export_pdf",
    }
    assert "applescript.run" not in names


@pytest.mark.asyncio
async def test_pages_append_text_uses_argv_not_script_text() -> None:
    runner = FakeRunner(
        applescript.AppleScriptResult(stdout='{"appended":true}', stderr="", returncode=0),
    )
    tool = _tool_by_name(pages.tools(runner=runner), "pages.append_text")
    malicious = 'hello"; do shell script "touch /tmp/bad'

    result = await tool.handler({"text": malicious})

    assert isinstance(result, dict)
    assert result["result"] == {"appended": True}
    script, argv, _timeout = runner.calls[0]
    assert malicious not in script
    assert argv == [malicious]


@pytest.mark.asyncio
async def test_numbers_set_cell_value_uses_argv_not_script_text() -> None:
    runner = FakeRunner(
        applescript.AppleScriptResult(stdout='{"updated":true}', stderr="", returncode=0),
    )
    tool = _tool_by_name(numbers.tools(runner=runner), "numbers.set_cell_value")
    malicious = '123"; do shell script "touch /tmp/bad'

    result = await tool.handler({"cell": "B2", "value": malicious})

    assert isinstance(result, dict)
    assert result["result"] == {"updated": True}
    script, argv, _timeout = runner.calls[0]
    assert malicious not in script
    assert argv == ["", "", "B2", malicious]


def test_specialized_macos_server_exposes_bounded_tools() -> None:
    names = _tool_names(macos.tools(runner=FakeRunner()))

    assert names == {
        "macos.frontmost_application",
        "macos.list_running_applications",
        "macos.open_application",
        "macos.get_clipboard_text",
        "macos.set_clipboard_text",
        "macos.show_notification",
    }
    assert "applescript.run" not in names


def test_specialized_outlook_server_exposes_bounded_tools() -> None:
    names = _tool_names(outlook.tools(runner=FakeRunner()))

    assert names == {
        "outlook.list_accounts",
        "outlook.create_draft",
    }
    assert "applescript.run" not in names


def test_specialized_word_server_exposes_bounded_tools() -> None:
    names = _tool_names(word.tools(runner=FakeRunner()))

    assert names == {
        "word.frontmost_document",
        "word.document_text",
        "word.append_text",
        "word.export_pdf",
    }
    assert "applescript.run" not in names


def test_specialized_powerpoint_server_exposes_bounded_tools() -> None:
    names = _tool_names(powerpoint.tools(runner=FakeRunner()))

    assert names == {
        "powerpoint.frontmost_presentation",
        "powerpoint.list_slides",
        "powerpoint.append_speaker_notes",
        "powerpoint.start_slideshow",
        "powerpoint.export_pdf",
    }
    assert "applescript.run" not in names


@pytest.mark.asyncio
async def test_specialized_apple_mail_create_draft_uses_argv_not_script_text() -> None:
    runner = FakeRunner(
        applescript.AppleScriptResult(
            stdout='{"created":true,"visible":true,"sent":false}',
            stderr="",
            returncode=0,
        ),
    )
    tool = _tool_by_name(apple_mail.tools(runner=runner), "apple_mail.create_draft")
    malicious_body = 'hello"; do shell script "touch /tmp/bad'

    result = await tool.handler(
        {
            "to": "friend@example.com",
            "subject": "Draft",
            "body": malicious_body,
        },
    )

    assert isinstance(result, dict)
    assert result["result"] == {"created": True, "visible": True, "sent": False}
    assert len(runner.calls) == 1
    script, argv, _timeout = runner.calls[0]
    assert malicious_body not in script
    assert argv == ["friend@example.com", "Draft", malicious_body, "", ""]


@pytest.mark.asyncio
async def test_specialized_outlook_create_draft_uses_argv_not_script_text() -> None:
    runner = FakeRunner(
        applescript.AppleScriptResult(
            stdout='{"created":true,"visible":true,"sent":false}',
            stderr="",
            returncode=0,
        ),
    )
    tool = _tool_by_name(outlook.tools(runner=runner), "outlook.create_draft")
    malicious_body = 'hello"; do shell script "touch /tmp/bad'

    result = await tool.handler(
        {
            "to": "friend@example.com",
            "subject": "Draft",
            "body": malicious_body,
        },
    )

    assert isinstance(result, dict)
    assert result["result"] == {"created": True, "visible": True, "sent": False}
    script, argv, _timeout = runner.calls[0]
    assert malicious_body not in script
    assert argv == ["friend@example.com", "Draft", malicious_body, "", ""]


@pytest.mark.asyncio
async def test_word_append_text_uses_argv_not_script_text() -> None:
    runner = FakeRunner(
        applescript.AppleScriptResult(stdout='{"appended":true}', stderr="", returncode=0),
    )
    tool = _tool_by_name(word.tools(runner=runner), "word.append_text")
    malicious = 'hello"; do shell script "touch /tmp/bad'

    result = await tool.handler({"text": malicious})

    assert isinstance(result, dict)
    assert result["result"] == {"appended": True}
    script, argv, _timeout = runner.calls[0]
    assert malicious not in script
    assert argv == [malicious]


@pytest.mark.asyncio
async def test_powerpoint_append_notes_uses_argv_not_script_text() -> None:
    runner = FakeRunner(
        applescript.AppleScriptResult(stdout='{"updated":true}', stderr="", returncode=0),
    )
    tool = _tool_by_name(
        powerpoint.tools(runner=runner),
        "powerpoint.append_speaker_notes",
    )
    malicious = 'notes"; do shell script "touch /tmp/bad'

    result = await tool.handler({"slide_number": 2, "notes": malicious})

    assert isinstance(result, dict)
    assert result["result"] == {"updated": True}
    script, argv, _timeout = runner.calls[0]
    assert malicious not in script
    assert argv == ["2", malicious]
