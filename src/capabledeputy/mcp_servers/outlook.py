"""Standalone MCP server: bounded Microsoft Outlook automation."""

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

SERVER_NAME = "capdep-outlook"

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

on trim_text(valueText)
  set textValue to valueText as text
  repeat while textValue starts with space
    set textValue to text 2 thru -1 of textValue
  end repeat
  repeat while textValue ends with space
    set textValue to text 1 thru -2 of textValue
  end repeat
  return textValue
end trim_text

on split_text(valueText, delimiterText)
  set oldDelims to AppleScript's text item delimiters
  set AppleScript's text item delimiters to delimiterText
  set parts to text items of valueText
  set AppleScript's text item delimiters to oldDelims
  return parts
end split_text
'''

_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "outlook.list_accounts",
        "app": "Microsoft Outlook",
        "bundle_id": "com.microsoft.Outlook",
        "description": "List Outlook accounts visible to the current macOS user.",
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
  set output to "["
  tell application "Microsoft Outlook"
    set accountNames to name of every account
  end tell
  repeat with i from 1 to count of accountNames
    if i > 1 then set output to output & ","
    set output to output & "{{\"name\":" & my json_string(item i of accountNames) & "}}"
  end repeat
  return output & "]"
end run

{_JSON_HELPERS}
""",
    },
    {
        "name": "outlook.create_draft",
        "app": "Microsoft Outlook",
        "bundle_id": "com.microsoft.Outlook",
        "description": "Create a visible Outlook draft without sending it.",
        "read_only": False,
        "destructive": False,
        "output_format": "json",
        "timeout_seconds": 15,
        "argv": ["to", "subject", "body", "cc", "bcc"],
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "cc": {"type": "string", "default": ""},
                "bcc": {"type": "string", "default": ""},
            },
            "required": ["to", "subject", "body"],
            "additionalProperties": False,
        },
        "script": f"""
on run argv
  set toText to item 1 of argv
  set subjectText to item 2 of argv
  set bodyText to item 3 of argv
  set ccText to item 4 of argv
  set bccText to item 5 of argv
  tell application "Microsoft Outlook"
    activate
    set draftMessage to make new outgoing message with properties ¬
      {{subject:subjectText, content:bodyText}}
    my add_recipients(toText, "to", draftMessage)
    my add_recipients(ccText, "cc", draftMessage)
    my add_recipients(bccText, "bcc", draftMessage)
    open draftMessage
  end tell
  return "{{\"created\":true,\"visible\":true,\"sent\":false}}"
end run

on add_recipients(addressText, recipientKind, draftMessage)
  if addressText is "" then return
  repeat with rawAddress in my split_text(addressText, ",")
    set addressValue to my trim_text(rawAddress)
    if addressValue is not "" then
      tell application "Microsoft Outlook"
        if recipientKind is "to" then
          make new recipient at draftMessage with properties ¬
            {{email address:{{address:addressValue}}, type:to recipient}}
        else if recipientKind is "cc" then
          make new recipient at draftMessage with properties ¬
            {{email address:{{address:addressValue}}, type:cc recipient}}
        else if recipientKind is "bcc" then
          make new recipient at draftMessage with properties ¬
            {{email address:{{address:addressValue}}, type:bcc recipient}}
        end if
      end tell
    end if
  end repeat
end add_recipients

{_JSON_HELPERS}
""",
    },
]


def tools(*, runner: AppleScriptRunner = run_osascript) -> list[ToolDescriptor]:
    specs = specs_from_dicts(_TOOL_SPECS, source_file="builtin:outlook")
    return descriptors_from_specs(specs, runner)


async def serve() -> None:
    await serve_tools(SERVER_NAME, tools())


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
