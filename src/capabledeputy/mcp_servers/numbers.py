"""Standalone MCP server: specialized Numbers automation.

This server focuses on personal spreadsheet workflows: inspect the open
spreadsheet, list sheets, read or set cells, and export to PDF. It does not
expose generic AppleScript execution.

Run via:
  capdep mcp-server-numbers
  python -m capabledeputy.mcp_servers.numbers
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

SERVER_NAME = "capdep-numbers"

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

_TABLE_RESOLUTION_HELPERS = r"""
on resolve_sheet(sheetName)
  tell application "Numbers"
    if sheetName is "" then
      return sheet 1 of front document
    end if
    return sheet sheetName of front document
  end tell
end resolve_sheet

on resolve_table(sheetObject, tableName)
  tell application "Numbers"
    if tableName is "" then
      return table 1 of sheetObject
    end if
    return table tableName of sheetObject
  end tell
end resolve_table
"""

_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "numbers.frontmost_document",
        "app": "Numbers",
        "bundle_id": "com.apple.iWork.Numbers",
        "description": "Return metadata about the frontmost open Numbers spreadsheet.",
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
  tell application "Numbers"
    if not (exists front document) then
      return "{{\"open\":false}}"
    end if
    set docName to name of front document
    set sheetCount to count of sheets of front document
  end tell
  return "{{\"open\":true,\"name\":" & my json_string(docName) & ¬
    ",\"sheet_count\":" & sheetCount & "}}"
end run

{_JSON_HELPERS}
""",
    },
    {
        "name": "numbers.list_sheets",
        "app": "Numbers",
        "bundle_id": "com.apple.iWork.Numbers",
        "description": "List sheet names and table counts in the frontmost spreadsheet.",
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
  tell application "Numbers"
    if not (exists front document) then
      return "{{\"open\":false,\"sheets\":[]}}"
    end if
    set output to "{{\"open\":true,\"sheets\":["
    set sheetCount to count of sheets of front document
    repeat with i from 1 to sheetCount
      if i > 1 then set output to output & ","
      set sheetObject to sheet i of front document
      set output to output & "{{\"name\":" & my json_string(name of sheetObject)
      set output to output & ",\"table_count\":" & (count of tables of sheetObject) & "}}"
    end repeat
  end tell
  return output & "]}}"
end run

{_JSON_HELPERS}
""",
    },
    {
        "name": "numbers.table_summary",
        "app": "Numbers",
        "bundle_id": "com.apple.iWork.Numbers",
        "description": "Return row and column counts for a table in the frontmost spreadsheet.",
        "read_only": True,
        "destructive": False,
        "output_format": "json",
        "timeout_seconds": 10,
        "argv": ["sheet_name", "table_name"],
        "input_schema": {
            "type": "object",
            "properties": {
                "sheet_name": {"type": "string", "default": ""},
                "table_name": {"type": "string", "default": ""},
            },
            "additionalProperties": False,
        },
        "script": f"""
on run argv
  set sheetName to item 1 of argv
  set tableName to item 2 of argv
  tell application "Numbers"
    if not (exists front document) then error "no front Numbers document"
  end tell
  set sheetObject to my resolve_sheet(sheetName)
  set tableObject to my resolve_table(sheetObject, tableName)
  tell application "Numbers"
    set resolvedSheet to name of sheetObject
    set resolvedTable to name of tableObject
    set rowCount to count of rows of tableObject
    set columnCount to count of columns of tableObject
  end tell
  return "{{\"sheet\":" & my json_string(resolvedSheet) & ¬
    ",\"table\":" & my json_string(resolvedTable) & ¬
    ",\"rows\":" & rowCount & ",\"columns\":" & columnCount & "}}"
end run

{_TABLE_RESOLUTION_HELPERS}
{_JSON_HELPERS}
""",
    },
    {
        "name": "numbers.get_cell_value",
        "app": "Numbers",
        "bundle_id": "com.apple.iWork.Numbers",
        "description": "Read a cell value from a table in the frontmost spreadsheet.",
        "read_only": True,
        "destructive": False,
        "output_format": "json",
        "timeout_seconds": 10,
        "argv": ["sheet_name", "table_name", "cell"],
        "input_schema": {
            "type": "object",
            "properties": {
                "sheet_name": {"type": "string", "default": ""},
                "table_name": {"type": "string", "default": ""},
                "cell": {"type": "string"},
            },
            "required": ["cell"],
            "additionalProperties": False,
        },
        "script": f"""
on run argv
  set sheetName to item 1 of argv
  set tableName to item 2 of argv
  set cellRef to item 3 of argv
  tell application "Numbers"
    if not (exists front document) then error "no front Numbers document"
  end tell
  set sheetObject to my resolve_sheet(sheetName)
  set tableObject to my resolve_table(sheetObject, tableName)
  tell application "Numbers"
    set cellValue to value of cell cellRef of tableObject
    set resolvedSheet to name of sheetObject
    set resolvedTable to name of tableObject
  end tell
  return "{{\"sheet\":" & my json_string(resolvedSheet) & ¬
    ",\"table\":" & my json_string(resolvedTable) & ¬
    ",\"cell\":" & my json_string(cellRef) & ¬
    ",\"value\":" & my json_string(cellValue as text) & "}}"
end run

{_TABLE_RESOLUTION_HELPERS}
{_JSON_HELPERS}
""",
    },
    {
        "name": "numbers.set_cell_value",
        "app": "Numbers",
        "bundle_id": "com.apple.iWork.Numbers",
        "description": "Set a cell value in a table in the frontmost spreadsheet.",
        "read_only": False,
        "destructive": True,
        "output_format": "json",
        "timeout_seconds": 10,
        "argv": ["sheet_name", "table_name", "cell", "value"],
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "input_schema": {
            "type": "object",
            "properties": {
                "sheet_name": {"type": "string", "default": ""},
                "table_name": {"type": "string", "default": ""},
                "cell": {"type": "string"},
                "value": {"type": "string"},
            },
            "required": ["cell", "value"],
            "additionalProperties": False,
        },
        "script": f"""
on run argv
  set sheetName to item 1 of argv
  set tableName to item 2 of argv
  set cellRef to item 3 of argv
  set valueText to item 4 of argv
  tell application "Numbers"
    if not (exists front document) then error "no front Numbers document"
  end tell
  set sheetObject to my resolve_sheet(sheetName)
  set tableObject to my resolve_table(sheetObject, tableName)
  tell application "Numbers"
    set value of cell cellRef of tableObject to valueText
    set resolvedSheet to name of sheetObject
    set resolvedTable to name of tableObject
  end tell
  return "{{\"updated\":true,\"sheet\":" & my json_string(resolvedSheet) & ¬
    ",\"table\":" & my json_string(resolvedTable) & ¬
    ",\"cell\":" & my json_string(cellRef) & "}}"
end run

{_TABLE_RESOLUTION_HELPERS}
{_JSON_HELPERS}
""",
    },
    {
        "name": "numbers.export_pdf",
        "app": "Numbers",
        "bundle_id": "com.apple.iWork.Numbers",
        "description": "Export the frontmost Numbers spreadsheet to a local PDF path.",
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
  tell application "Numbers"
    if not (exists front document) then error "no front Numbers document"
    export front document to file (POSIX file pdfPath) as PDF
  end tell
  return "{{\"exported\":true,\"path\":" & my json_string(pdfPath) & "}}"
end run

{_JSON_HELPERS}
""",
    },
]


def tools(*, runner: AppleScriptRunner = run_osascript) -> list[ToolDescriptor]:
    specs = specs_from_dicts(_TOOL_SPECS, source_file="builtin:numbers")
    return descriptors_from_specs(specs, runner)


async def serve() -> None:
    await serve_tools(SERVER_NAME, tools())


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
