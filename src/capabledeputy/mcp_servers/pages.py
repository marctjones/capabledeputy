"""Standalone MCP server: specialized Pages automation.

This server focuses on personal document workflows: inspect the open
document, read its body text, append text, and export to PDF. It does not
expose generic AppleScript execution.

Run via:
  capdep mcp-server-pages
  python -m capabledeputy.mcp_servers.pages
"""

from __future__ import annotations

import asyncio
from typing import Any

from capabledeputy.mcp_servers._common import ToolDescriptor, serve_tools
from capabledeputy.mcp_servers.applescript import (
    AppleScriptRunner,
    descriptors_from_specs,
    run_osascript,
    specs_from_dicts,
)

SERVER_NAME = "capdep-pages"

_JSON_HELPERS = r'''
on json_string(valueText)
  set escaped to valueText as text
  set escaped to my replace_text(escaped, "\", "\\")
  set escaped to my replace_text(escaped, """", "\""")
  set escaped to my replace_text(escaped, return, "\n")
  set escaped to my replace_text(escaped, linefeed, "\n")
  return """" & escaped & """"
end json_string

on replace_text(valueText, searchText, replacementText)
  set oldDelims to AppleScript's text item delimiters
  set AppleScript's text item delimiters to searchText
  set parts to text items of valueText
  set AppleScript's text item delimiters to replacementText
  set newText to parts as text
  set AppleScript's text item delimiters to oldDelims
  return newText
end replace_text
'''

_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "pages.frontmost_document",
        "app": "Pages",
        "bundle_id": "com.apple.iWork.Pages",
        "description": "Return metadata about the frontmost open Pages document.",
        "read_only": True,
        "destructive": False,
        "output_format": "json",
        "timeout_seconds": 10,
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "script": f"""
on run argv
  tell application "Pages"
    if not (exists front document) then
      return "{{\"open\":false}}"
    end if
    set docName to name of front document
    set bodyText to body text of front document as text
    set wordCount to count of words of bodyText
    set charCount to length of bodyText
  end tell
  return "{{\"open\":true,\"name\":" & my json_string(docName) & ¬
    ",\"word_count\":" & wordCount & ",\"character_count\":" & charCount & "}}"
end run

{_JSON_HELPERS}
""",
    },
    {
        "name": "pages.document_text",
        "app": "Pages",
        "bundle_id": "com.apple.iWork.Pages",
        "description": "Read the body text of the frontmost Pages document.",
        "read_only": True,
        "destructive": False,
        "output_format": "json",
        "timeout_seconds": 15,
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "script": f"""
on run argv
  tell application "Pages"
    if not (exists front document) then
      return "{{\"open\":false,\"text\":\"\"}}"
    end if
    set docName to name of front document
    set bodyText to body text of front document as text
  end tell
  return "{{\"open\":true,\"name\":" & my json_string(docName) & ¬
    ",\"text\":" & my json_string(bodyText) & "}}"
end run

{_JSON_HELPERS}
""",
    },
    {
        "name": "pages.append_text",
        "app": "Pages",
        "bundle_id": "com.apple.iWork.Pages",
        "description": "Append text to the body of the frontmost Pages document.",
        "read_only": False,
        "destructive": True,
        "output_format": "json",
        "timeout_seconds": 15,
        "argv": ["text"],
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
        "script": """
on run argv
  set appendText to item 1 of argv
  tell application "Pages"
    if not (exists front document) then error "no front Pages document"
    set currentText to body text of front document as text
    set body text of front document to currentText & appendText
  end tell
  return "{\"appended\":true}"
end run
""",
    },
    {
        "name": "pages.export_pdf",
        "app": "Pages",
        "bundle_id": "com.apple.iWork.Pages",
        "description": "Export the frontmost Pages document to a local PDF path.",
        "read_only": False,
        "destructive": False,
        "output_format": "json",
        "timeout_seconds": 30,
        "argv": ["path"],
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        "script": f"""
on run argv
  set pdfPath to item 1 of argv
  if pdfPath does not end with ".pdf" then error "path must end with .pdf"
  tell application "Pages"
    if not (exists front document) then error "no front Pages document"
    export front document to file (POSIX file pdfPath) as PDF
  end tell
  return "{{\"exported\":true,\"path\":" & my json_string(pdfPath) & "}}"
end run

{_JSON_HELPERS}
""",
    },
]


def tools(*, runner: AppleScriptRunner = run_osascript) -> list[ToolDescriptor]:
    specs = specs_from_dicts(_TOOL_SPECS, source_file="builtin:pages")
    return descriptors_from_specs(specs, runner)


async def serve() -> None:
    await serve_tools(SERVER_NAME, tools())


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
