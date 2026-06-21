"""Standalone MCP server: bounded general macOS automation.

The server exposes safe local desktop primitives for personal workflows:
foreground app inspection, application activation, clipboard access, and
notifications. It does not expose arbitrary AppleScript.

Run via:
  capdep mcp-server-macos
  python -m capabledeputy.mcp_servers.macos
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

SERVER_NAME = "capdep-macos"

_JSON_HELPERS = r'''
on json_string(valueText)
  set escaped to valueText as text
  set escaped to my replace_text(escaped, "\", "\\")
  set escaped to my replace_text(escaped, """", "\""")
  set escaped to my replace_text(escaped, return, "\n")
  set escaped to my replace_text(escaped, linefeed, "\n")
  return """" & escaped & """"
end json_string

on json_bool(valueFlag)
  if valueFlag then
    return "true"
  end if
  return "false"
end json_bool

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
        "name": "macos.frontmost_application",
        "app": "System Events",
        "bundle_id": "com.apple.systemevents",
        "description": "Return the current frontmost macOS application.",
        "read_only": True,
        "destructive": False,
        "output_format": "json",
        "timeout_seconds": 5,
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "script": f"""
on run argv
  tell application "System Events"
    set frontApp to first application process whose frontmost is true
    set appName to name of frontApp
    set bundleId to ""
    try
      set bundleId to bundle identifier of frontApp
    end try
  end tell
  return "{{\"name\":" & my json_string(appName) & ",\"bundle_id\":"
  return result & my json_string(bundleId) & "}}"
end run

{_JSON_HELPERS}
""",
    },
    {
        "name": "macos.list_running_applications",
        "app": "System Events",
        "bundle_id": "com.apple.systemevents",
        "description": "List visible running macOS applications.",
        "read_only": True,
        "destructive": False,
        "output_format": "json",
        "timeout_seconds": 10,
        "argv": ["limit"],
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 50},
            },
            "additionalProperties": False,
        },
        "script": f"""
on run argv
  set maxItems to item 1 of argv as integer
  set output to "{{\"applications\":["
  set emitted to 0
  tell application "System Events"
    set appProcesses to application processes whose background only is false
    repeat with appProcess in appProcesses
      if emitted >= maxItems then exit repeat
      if emitted > 0 then set output to output & ","
      set appName to name of appProcess
      set bundleId to ""
      set isFrontmost to frontmost of appProcess
      try
        set bundleId to bundle identifier of appProcess
      end try
      set output to output & "{{\"name\":" & my json_string(appName)
      set output to output & ",\"bundle_id\":" & my json_string(bundleId)
      set output to output & ",\"frontmost\":" & my json_bool(isFrontmost) & "}}"
      set emitted to emitted + 1
    end repeat
  end tell
  return output & "]}}"
end run

{_JSON_HELPERS}
""",
    },
    {
        "name": "macos.open_application",
        "app": "Finder",
        "bundle_id": "com.apple.finder",
        "description": "Open or focus a macOS application by bundle id.",
        "read_only": False,
        "destructive": False,
        "output_format": "json",
        "timeout_seconds": 10,
        "argv": ["bundle_id"],
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "input_schema": {
            "type": "object",
            "properties": {
                "bundle_id": {"type": "string"},
            },
            "required": ["bundle_id"],
            "additionalProperties": False,
        },
        "script": f"""
on run argv
  set requestedBundleId to item 1 of argv
  if requestedBundleId does not contain "." then
    error "bundle_id must look like a reverse-DNS application id"
  end if
  do shell script "/usr/bin/open -b " & quoted form of requestedBundleId
  return "{{\"opened\":true,\"bundle_id\":" & my json_string(requestedBundleId) & "}}"
end run

{_JSON_HELPERS}
""",
    },
    {
        "name": "macos.get_clipboard_text",
        "app": "System Events",
        "bundle_id": "com.apple.systemevents",
        "description": "Read the current macOS clipboard as text.",
        "read_only": True,
        "destructive": False,
        "output_format": "json",
        "timeout_seconds": 5,
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "script": f"""
on run argv
  set clipText to the clipboard as text
  return "{{\"text\":" & my json_string(clipText) & "}}"
end run

{_JSON_HELPERS}
""",
    },
    {
        "name": "macos.set_clipboard_text",
        "app": "System Events",
        "bundle_id": "com.apple.systemevents",
        "description": "Replace the current macOS clipboard text.",
        "read_only": False,
        "destructive": True,
        "output_format": "json",
        "timeout_seconds": 5,
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
  set the clipboard to item 1 of argv
  return "{\"updated\":true}"
end run
""",
    },
    {
        "name": "macos.show_notification",
        "app": "System Events",
        "bundle_id": "com.apple.systemevents",
        "description": "Display a local macOS notification.",
        "read_only": False,
        "destructive": False,
        "output_format": "json",
        "timeout_seconds": 5,
        "argv": ["title", "message"],
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "default": "CapableDeputy"},
                "message": {"type": "string"},
            },
            "required": ["message"],
            "additionalProperties": False,
        },
        "script": f"""
on run argv
  set titleText to item 1 of argv
  set messageText to item 2 of argv
  display notification messageText with title titleText
  return "{{\"notified\":true,\"title\":" & my json_string(titleText) & "}}"
end run

{_JSON_HELPERS}
""",
    },
]


def tools(*, runner: AppleScriptRunner = run_osascript) -> list[ToolDescriptor]:
    specs = specs_from_dicts(_TOOL_SPECS, source_file="builtin:macos")
    return descriptors_from_specs(specs, runner)


async def serve() -> None:
    await serve_tools(SERVER_NAME, tools())


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
