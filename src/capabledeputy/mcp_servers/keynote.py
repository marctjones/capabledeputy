"""Standalone MCP server: specialized Keynote automation.

This server focuses on personal presentation workflows: inspect the open deck,
read slide text, and start or stop presenting. It does not expose generic
AppleScript execution.

Run via:
  capdep mcp-server-keynote
  python -m capabledeputy.mcp_servers.keynote
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

SERVER_NAME = "capdep-keynote"

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

_SLIDE_HELPERS = r'''
on slide_title(slideObject)
  try
    return object text of default title item of slideObject as text
  end try
  return ""
end slide_title

on slide_body(slideObject)
  try
    return object text of default body item of slideObject as text
  end try
  return ""
end slide_body

on slide_notes(slideObject)
  try
    return presenter notes of slideObject as text
  end try
  return ""
end slide_notes
'''

_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "keynote.frontmost_document",
        "app": "Keynote",
        "bundle_id": "com.apple.iWork.Keynote",
        "description": "Return metadata about the frontmost open Keynote document.",
        "read_only": True,
        "destructive": False,
        "output_format": "json",
        "timeout_seconds": 10,
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "script": f'''
on run argv
  tell application "Keynote"
    if not (exists front document) then
      return "{{\"open\":false}}"
    end if
    set docName to name of front document
    set slideCount to count of slides of front document
  end tell
  return "{{\"open\":true,\"name\":" & my json_string(docName) & ¬
    ",\"slide_count\":" & slideCount & "}}"
end run

{_JSON_HELPERS}
''',
    },
    {
        "name": "keynote.list_slides",
        "app": "Keynote",
        "bundle_id": "com.apple.iWork.Keynote",
        "description": "List slide numbers and visible title/body text in the frontmost deck.",
        "read_only": True,
        "destructive": False,
        "output_format": "json",
        "timeout_seconds": 15,
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "script": f'''
on run argv
  tell application "Keynote"
    if not (exists front document) then
      return "{{\"open\":false,\"slides\":[]}}"
    end if
    set slideCount to count of slides of front document
    set output to "{{\"open\":true,\"slides\":["
    repeat with i from 1 to slideCount
      if i > 1 then set output to output & ","
      set slideObject to slide i of front document
      set output to output & "{{\"number\":" & i
      set output to output & ",\"title\":" & my json_string(my slide_title(slideObject))
      set output to output & ",\"body\":"
      set output to output & my json_string(my slide_body(slideObject)) & "}}"
    end repeat
  end tell
  return output & "]}}"
end run

{_SLIDE_HELPERS}
{_JSON_HELPERS}
''',
    },
    {
        "name": "keynote.slide_text",
        "app": "Keynote",
        "bundle_id": "com.apple.iWork.Keynote",
        "description": "Read title, body, and presenter notes from one slide.",
        "read_only": True,
        "destructive": False,
        "output_format": "json",
        "timeout_seconds": 10,
        "argv": ["slide_number"],
        "input_schema": {
            "type": "object",
            "properties": {
                "slide_number": {"type": "integer"},
            },
            "required": ["slide_number"],
            "additionalProperties": False,
        },
        "script": f'''
on run argv
  set requestedSlide to item 1 of argv as integer
  tell application "Keynote"
    if not (exists front document) then
      return "{{\"open\":false}}"
    end if
    set slideCount to count of slides of front document
    if requestedSlide < 1 or requestedSlide > slideCount then
      error "slide_number is outside the open document's slide range"
    end if
    set slideObject to slide requestedSlide of front document
    set output to "{{\"open\":true,\"number\":" & requestedSlide
    set output to output & ",\"title\":" & my json_string(my slide_title(slideObject))
    set output to output & ",\"body\":" & my json_string(my slide_body(slideObject))
    set output to output & ",\"presenter_notes\":"
    set output to output & my json_string(my slide_notes(slideObject)) & "}}"
  end tell
  return output
end run

{_SLIDE_HELPERS}
{_JSON_HELPERS}
''',
    },
    {
        "name": "keynote.start_slideshow",
        "app": "Keynote",
        "bundle_id": "com.apple.iWork.Keynote",
        "description": "Start playing the frontmost Keynote deck from a slide number.",
        "read_only": False,
        "destructive": False,
        "output_format": "json",
        "timeout_seconds": 10,
        "argv": ["slide_number"],
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "input_schema": {
            "type": "object",
            "properties": {
                "slide_number": {"type": "integer", "default": 1},
            },
            "additionalProperties": False,
        },
        "script": '''
on run argv
  set requestedSlide to item 1 of argv as integer
  tell application "Keynote"
    if not (exists front document) then error "no front Keynote document"
    activate
    start from slide requestedSlide of front document
  end tell
  return "{\"started\":true,\"slide_number\":" & requestedSlide & "}"
end run
''',
    },
    {
        "name": "keynote.stop_slideshow",
        "app": "Keynote",
        "bundle_id": "com.apple.iWork.Keynote",
        "description": "Stop an active Keynote slideshow.",
        "read_only": False,
        "destructive": False,
        "output_format": "json",
        "timeout_seconds": 10,
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "script": '''
on run argv
  tell application "Keynote"
    stop
  end tell
  return "{\"stopped\":true}"
end run
''',
    },
]


def tools(*, runner: AppleScriptRunner = run_osascript) -> list[ToolDescriptor]:
    specs = specs_from_dicts(_TOOL_SPECS, source_file="builtin:keynote")
    return descriptors_from_specs(specs, runner)


async def serve() -> None:
    await serve_tools(SERVER_NAME, tools())


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
