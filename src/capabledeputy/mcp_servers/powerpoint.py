"""Standalone MCP server: bounded Microsoft PowerPoint automation."""

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

SERVER_NAME = "capdep-powerpoint"

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
        "name": "powerpoint.frontmost_presentation",
        "app": "Microsoft PowerPoint",
        "bundle_id": "com.microsoft.Powerpoint",
        "description": "Return metadata about the frontmost PowerPoint presentation.",
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
  tell application "Microsoft PowerPoint"
    if not (exists active presentation) then
      return "{{\"open\":false}}"
    end if
    set deckName to name of active presentation
    set slideCount to count of slides of active presentation
  end tell
  return "{{\"open\":true,\"name\":" & my json_string(deckName) & ¬
    ",\"slide_count\":" & slideCount & "}}"
end run

{_JSON_HELPERS}
""",
    },
    {
        "name": "powerpoint.list_slides",
        "app": "Microsoft PowerPoint",
        "bundle_id": "com.microsoft.Powerpoint",
        "description": "List slide numbers in the active PowerPoint presentation.",
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
  tell application "Microsoft PowerPoint"
    if not (exists active presentation) then
      return "{{\"open\":false,\"slides\":[]}}"
    end if
    set slideCount to count of slides of active presentation
  end tell
  set output to "{{\"open\":true,\"slides\":["
  repeat with i from 1 to slideCount
    if i > 1 then set output to output & ","
    set output to output & "{{\"number\":" & i & "}}"
  end repeat
  return output & "]}}"
end run

{_JSON_HELPERS}
""",
    },
    {
        "name": "powerpoint.append_speaker_notes",
        "app": "Microsoft PowerPoint",
        "bundle_id": "com.microsoft.Powerpoint",
        "description": "Append speaker notes to one slide in the active presentation.",
        "read_only": False,
        "destructive": True,
        "output_format": "json",
        "timeout_seconds": 15,
        "argv": ["slide_number", "notes"],
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "input_schema": {
            "type": "object",
            "properties": {
                "slide_number": {"type": "integer"},
                "notes": {"type": "string"},
            },
            "required": ["slide_number", "notes"],
            "additionalProperties": False,
        },
        "script": f"""
on run argv
  set requestedSlide to item 1 of argv as integer
  set notesText to item 2 of argv
  tell application "Microsoft PowerPoint"
    if not (exists active presentation) then error "no active PowerPoint presentation"
    set slideCount to count of slides of active presentation
    if requestedSlide < 1 or requestedSlide > slideCount then error "slide_number is outside range"
    set notes page of slide requestedSlide of active presentation to notesText
  end tell
  return "{{\"updated\":true,\"slide_number\":" & requestedSlide & "}}"
end run

{_JSON_HELPERS}
""",
    },
    {
        "name": "powerpoint.start_slideshow",
        "app": "Microsoft PowerPoint",
        "bundle_id": "com.microsoft.Powerpoint",
        "description": "Start presenting the active PowerPoint deck.",
        "read_only": False,
        "destructive": False,
        "output_format": "json",
        "timeout_seconds": 10,
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "script": """
on run argv
  tell application "Microsoft PowerPoint"
    if not (exists active presentation) then error "no active PowerPoint presentation"
    start slide show active presentation
  end tell
  return "{\"started\":true}"
end run
""",
    },
    {
        "name": "powerpoint.export_pdf",
        "app": "Microsoft PowerPoint",
        "bundle_id": "com.microsoft.Powerpoint",
        "description": "Export the active PowerPoint presentation to a local PDF path.",
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
  tell application "Microsoft PowerPoint"
    if not (exists active presentation) then error "no active PowerPoint presentation"
    save active presentation in pdfPath as save as PDF
  end tell
  return "{{\"exported\":true,\"path\":" & my json_string(pdfPath) & "}}"
end run

{_JSON_HELPERS}
""",
    },
]


def tools(*, runner: AppleScriptRunner = run_osascript) -> list[ToolDescriptor]:
    specs = specs_from_dicts(_TOOL_SPECS, source_file="builtin:powerpoint")
    return descriptors_from_specs(specs, runner)


async def serve() -> None:
    await serve_tools(SERVER_NAME, tools())


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
