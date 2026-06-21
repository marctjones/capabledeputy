"""Standalone MCP server: specialized Apple Mail automation.

The server exposes bounded Mail workflows only. It never exposes generic
AppleScript execution and intentionally creates visible drafts instead of
sending messages directly.

Run via:
  capdep mcp-server-apple-mail
  python -m capabledeputy.mcp_servers.apple_mail
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

SERVER_NAME = "capdep-apple-mail"

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

_MAILBOX_HELPERS = r"""
on resolve_mailbox(mailboxName)
  tell application "Mail"
    repeat with acct in accounts
      try
        return mailbox mailboxName of acct
      end try
    end repeat
    try
      return mailbox mailboxName
    end try
  end tell
  error "mailbox not found: " & mailboxName
end resolve_mailbox
"""

_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "apple_mail.list_accounts",
        "app": "Mail",
        "bundle_id": "com.apple.mail",
        "description": "List Apple Mail account names visible to the current macOS user.",
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
  tell application "Mail"
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
        "name": "apple_mail.list_mailboxes",
        "app": "Mail",
        "bundle_id": "com.apple.mail",
        "description": "List Apple Mail account mailbox names.",
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
  set output to "["
  set rowCount to 0
  tell application "Mail"
    repeat with acct in accounts
      set acctName to name of acct
      repeat with mb in mailboxes of acct
        if rowCount > 0 then set output to output & ","
        set output to output & "{{\"account\":" & my json_string(acctName)
        set output to output & ",\"name\":" & my json_string(name of mb) & "}}"
        set rowCount to rowCount + 1
      end repeat
    end repeat
  end tell
  return output & "]"
end run

{_JSON_HELPERS}
""",
    },
    {
        "name": "apple_mail.search_messages",
        "app": "Mail",
        "bundle_id": "com.apple.mail",
        "description": (
            "Search subject/sender metadata in a named Apple Mail mailbox. "
            "Set include_body only for deliberate message-content reads."
        ),
        "read_only": True,
        "destructive": False,
        "output_format": "json",
        "timeout_seconds": 20,
        "argv": ["mailbox_name", "query", "limit", "include_body"],
        "input_schema": {
            "type": "object",
            "properties": {
                "mailbox_name": {"type": "string", "default": "INBOX"},
                "query": {"type": "string", "default": ""},
                "limit": {"type": "integer", "default": 10},
                "include_body": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
        "script": f"""
on run argv
  set mailboxName to item 1 of argv
  set queryText to item 2 of argv
  set maxItems to item 3 of argv as integer
  set includeBody to item 4 of argv is "true"
  set targetMailbox to my resolve_mailbox(mailboxName)
  set output to "{{\"mailbox\":" & my json_string(mailboxName) & ",\"messages\":["
  set emitted to 0
  tell application "Mail"
    repeat with msg in messages of targetMailbox
      if emitted >= maxItems then exit repeat
      set senderText to sender of msg as text
      set subjectText to subject of msg as text
      if queryText is "" or subjectText contains queryText or senderText contains queryText then
        if emitted > 0 then set output to output & ","
        set output to output & "{{\"message_id\":"
        set output to output & my json_string(message id of msg as text)
        set output to output & ",\"subject\":" & my json_string(subjectText)
        set output to output & ",\"sender\":" & my json_string(senderText)
        set output to output & ",\"read\":" & my json_bool(read status of msg)
        set output to output & ",\"date_received\":"
        set output to output & my json_string(date received of msg as text)
        if includeBody then
          set output to output & ",\"body\":" & my json_string(content of msg as text)
        end if
        set output to output & "}}"
        set emitted to emitted + 1
      end if
    end repeat
  end tell
  return output & "]}}"
end run

{_MAILBOX_HELPERS}
{_JSON_HELPERS}
""",
    },
    {
        "name": "apple_mail.get_message",
        "app": "Mail",
        "bundle_id": "com.apple.mail",
        "description": "Read one Apple Mail message by mailbox and message id.",
        "read_only": True,
        "destructive": False,
        "output_format": "json",
        "timeout_seconds": 20,
        "argv": ["mailbox_name", "message_id", "include_body"],
        "input_schema": {
            "type": "object",
            "properties": {
                "mailbox_name": {"type": "string", "default": "INBOX"},
                "message_id": {"type": "string"},
                "include_body": {"type": "boolean", "default": True},
            },
            "required": ["message_id"],
            "additionalProperties": False,
        },
        "script": f"""
on run argv
  set mailboxName to item 1 of argv
  set wantedId to item 2 of argv
  set includeBody to item 3 of argv is "true"
  set targetMailbox to my resolve_mailbox(mailboxName)
  tell application "Mail"
    repeat with msg in messages of targetMailbox
      if (message id of msg as text) is wantedId then
        set output to "{{\"message_id\":" & my json_string(wantedId)
        set output to output & ",\"subject\":" & my json_string(subject of msg as text)
        set output to output & ",\"sender\":" & my json_string(sender of msg as text)
        set output to output & ",\"read\":" & my json_bool(read status of msg)
        set output to output & ",\"date_received\":"
        set output to output & my json_string(date received of msg as text)
        if includeBody then
          set output to output & ",\"body\":" & my json_string(content of msg as text)
        end if
        return output & "}}"
      end if
    end repeat
  end tell
  error "message not found: " & wantedId
end run

{_MAILBOX_HELPERS}
{_JSON_HELPERS}
""",
    },
    {
        "name": "apple_mail.create_draft",
        "app": "Mail",
        "bundle_id": "com.apple.mail",
        "description": "Create a visible Apple Mail draft without sending it.",
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
  tell application "Mail"
    activate
    set draftMessage to make new outgoing message with properties ¬
      {{subject:subjectText, content:bodyText, visible:true}}
    tell draftMessage
      my add_recipients(toText, "to", draftMessage)
      my add_recipients(ccText, "cc", draftMessage)
      my add_recipients(bccText, "bcc", draftMessage)
    end tell
  end tell
  return "{{\"created\":true,\"visible\":true,\"sent\":false}}"
end run

on add_recipients(addressText, recipientKind, draftMessage)
  if addressText is "" then return
  repeat with rawAddress in my split_text(addressText, ",")
    set addressValue to my trim_text(rawAddress)
    if addressValue is not "" then
      tell application "Mail"
        tell draftMessage
          if recipientKind is "to" then
            make new to recipient at end of to recipients with properties {{address:addressValue}}
          else if recipientKind is "cc" then
            make new cc recipient at end of cc recipients with properties {{address:addressValue}}
          else if recipientKind is "bcc" then
            make new bcc recipient at end of bcc recipients with properties {{address:addressValue}}
          end if
        end tell
      end tell
    end if
  end repeat
end add_recipients

{_JSON_HELPERS}
""",
    },
]


def tools(*, runner: AppleScriptRunner = run_osascript) -> list[ToolDescriptor]:
    specs = specs_from_dicts(_TOOL_SPECS, source_file="builtin:apple_mail")
    return descriptors_from_specs(specs, runner)


async def serve() -> None:
    await serve_tools(SERVER_NAME, tools())


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
