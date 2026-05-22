"""[DEPRECATED] Bundled MCP server: Google Workspace.

Superseded by the official Google CLI MCP server (`gws mcp` from
`@googleworkspace/cli`). New installs should run
`capdep gworkspace-setup` to wire the official CLI — that's what
the curated configs + the personal-assistant preset now ship.

This module is kept around for back-compat with existing
deployments that already wrote `command: ["capdep",
"mcp-server-gworkspace"]` into their config. New code should NOT
reference it. The module + its credentials helpers will be removed
in a future release; check the CHANGELOG before relying on this.

Pure Python; uses google-api-python-client. Operator runs
`capdep gworkspace-setup` once to consent + cache a refresh token,
then `capdep daemon start --config configs/curated/google-workspace.yaml`
spawns this server as an upstream.

Tools shipped:
  gmail.list_threads(query="", max_results=20)
  gmail.read_thread(thread_id)
  gmail.send(to, subject, body)
  gmail.search(query, max_results=20)
  docs.read(document_id)
  docs.create(title, body="")
  drive.list(query="", max_results=20)
  drive.read_file_content(file_id)
  calendar.list_events(time_min, time_max, max_results=20)
  calendar.create_event(summary, start, end, description="")

Inherent labels: this server adds NO inherent labels — operator's
config (configs/curated/google-workspace.yaml) attaches per-tool
labels (e.g. confidential.personal on gmail.* / calendar.*;
confidential.work on docs.* / drive.*).

Run via:
  capdep mcp-server-gworkspace
  python -m capabledeputy.mcp_servers.gworkspace
"""

from __future__ import annotations

import asyncio
from typing import Any

from capabledeputy.mcp_servers._common import ToolDescriptor, serve_tools
from capabledeputy.mcp_servers._gworkspace_auth import load_credentials

SERVER_NAME = "capdep-gworkspace"


def _build_service(api_name: str, api_version: str) -> Any:
    """Lazy-build a Google API service. Each tool call constructs its
    own — small per-call cost, but it avoids holding stale credentials
    across long-running daemons + lets each call refresh independently.
    """
    from googleapiclient.discovery import build

    creds = load_credentials()
    return build(api_name, api_version, credentials=creds, cache_discovery=False)


# ---------------- Gmail ----------------


async def _gmail_list_threads(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query", ""))
    max_results = int(args.get("max_results", 20))
    service = _build_service("gmail", "v1")
    result = (
        service.users()
        .threads()
        .list(
            userId="me",
            q=query if query else None,
            maxResults=min(max_results, 100),
        )
        .execute()
    )
    return {
        "query": query,
        "count": len(result.get("threads", [])),
        "threads": [
            {
                "id": t["id"],
                "snippet": t.get("snippet", "")[:200],
                "historyId": t.get("historyId"),
            }
            for t in result.get("threads", [])
        ],
    }


async def _gmail_read_thread(args: dict[str, Any]) -> dict[str, Any]:
    thread_id = str(args["thread_id"])
    service = _build_service("gmail", "v1")
    result = (
        service.users()
        .threads()
        .get(
            userId="me",
            id=thread_id,
            format="full",
        )
        .execute()
    )
    messages = []
    for m in result.get("messages", []):
        headers = {h["name"]: h["value"] for h in m.get("payload", {}).get("headers", [])}
        # Best-effort body extraction; Gmail returns nested parts
        body = _extract_body(m.get("payload", {}))
        messages.append(
            {
                "id": m["id"],
                "from": headers.get("From", ""),
                "to": headers.get("To", ""),
                "subject": headers.get("Subject", ""),
                "date": headers.get("Date", ""),
                "snippet": m.get("snippet", ""),
                "body": body[:8192],
            },
        )
    return {
        "thread_id": thread_id,
        "message_count": len(messages),
        "messages": messages,
    }


def _extract_body(payload: dict[str, Any]) -> str:
    import base64

    body_data = ""
    if "body" in payload and payload["body"].get("data"):
        body_data = payload["body"]["data"]
    else:
        for part in payload.get("parts", []):
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                body_data = part["body"]["data"]
                break
        if not body_data:
            for part in payload.get("parts", []):
                inner = _extract_body(part)
                if inner:
                    return inner
    if not body_data:
        return ""
    try:
        return base64.urlsafe_b64decode(body_data + "===").decode("utf-8", errors="replace")
    except Exception:
        return ""


async def _gmail_send(args: dict[str, Any]) -> dict[str, Any]:
    import base64
    from email.mime.text import MIMEText

    to = str(args["to"])
    subject = str(args["subject"])
    body = str(args["body"])
    msg = MIMEText(body)
    msg["to"] = to
    msg["subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    service = _build_service("gmail", "v1")
    sent = (
        service.users()
        .messages()
        .send(
            userId="me",
            body={"raw": raw},
        )
        .execute()
    )
    return {"id": sent.get("id"), "thread_id": sent.get("threadId"), "sent": True}


async def _gmail_search(args: dict[str, Any]) -> dict[str, Any]:
    # Search is just list_threads with a query — they share the API call.
    return await _gmail_list_threads(args)


# ---------------- Docs ----------------


async def _docs_read(args: dict[str, Any]) -> dict[str, Any]:
    document_id = str(args["document_id"])
    service = _build_service("docs", "v1")
    doc = service.documents().get(documentId=document_id).execute()
    text = _extract_doc_text(doc.get("body", {}).get("content", []))
    return {
        "document_id": document_id,
        "title": doc.get("title", ""),
        "revision": doc.get("revisionId", ""),
        "content": text[:65536],
    }


def _extract_doc_text(elements: list[Any]) -> str:
    parts: list[str] = []
    for element in elements:
        if "paragraph" in element:
            for run in element["paragraph"].get("elements", []):
                tr = run.get("textRun", {})
                if "content" in tr:
                    parts.append(tr["content"])
        elif "table" in element:
            for row in element["table"].get("tableRows", []):
                for cell in row.get("tableCells", []):
                    parts.append(_extract_doc_text(cell.get("content", [])))
    return "".join(parts)


async def _docs_create(args: dict[str, Any]) -> dict[str, Any]:
    title = str(args["title"])
    body = str(args.get("body", ""))
    service = _build_service("docs", "v1")
    doc = service.documents().create(body={"title": title}).execute()
    document_id = doc["documentId"]
    if body:
        service.documents().batchUpdate(
            documentId=document_id,
            body={"requests": [{"insertText": {"location": {"index": 1}, "text": body}}]},
        ).execute()
    return {
        "document_id": document_id,
        "title": title,
        "url": f"https://docs.google.com/document/d/{document_id}/edit",
        "created": True,
    }


# ---------------- Drive ----------------


async def _drive_list(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query", ""))
    max_results = int(args.get("max_results", 20))
    service = _build_service("drive", "v3")
    result = (
        service.files()
        .list(
            q=query if query else None,
            pageSize=min(max_results, 100),
            fields="files(id, name, mimeType, modifiedTime, owners(emailAddress))",
        )
        .execute()
    )
    return {
        "query": query,
        "count": len(result.get("files", [])),
        "files": result.get("files", []),
    }


async def _drive_read_file_content(args: dict[str, Any]) -> dict[str, Any]:
    file_id = str(args["file_id"])
    service = _build_service("drive", "v3")
    meta = service.files().get(fileId=file_id, fields="id, name, mimeType").execute()
    mime = meta.get("mimeType", "")
    # Google Docs/Sheets/Slides need export; binary files need get_media
    if mime.startswith("application/vnd.google-apps."):
        # Export as plain text for docs; CSV for sheets
        export_mime = "text/plain" if "document" in mime else "text/csv"
        try:
            content = (
                service.files()
                .export(
                    fileId=file_id,
                    mimeType=export_mime,
                )
                .execute()
            )
            text = (
                content.decode("utf-8", errors="replace")
                if isinstance(content, bytes)
                else str(content)
            )
        except Exception as e:
            return {"id": file_id, "name": meta.get("name"), "error": str(e)}
    else:
        try:
            content = service.files().get_media(fileId=file_id).execute()
            text = (
                content.decode("utf-8", errors="replace")
                if isinstance(content, bytes)
                else str(content)
            )
        except Exception as e:
            return {"id": file_id, "name": meta.get("name"), "error": str(e)}
    return {
        "id": file_id,
        "name": meta.get("name", ""),
        "mime_type": mime,
        "content": text[:65536],
    }


# ---------------- Calendar ----------------


async def _calendar_list_events(args: dict[str, Any]) -> dict[str, Any]:
    time_min = str(args.get("time_min", ""))
    time_max = str(args.get("time_max", ""))
    max_results = int(args.get("max_results", 20))
    calendar_id = str(args.get("calendar_id", "primary"))
    service = _build_service("calendar", "v3")
    kwargs: dict[str, Any] = {
        "calendarId": calendar_id,
        "maxResults": min(max_results, 100),
        "singleEvents": True,
        "orderBy": "startTime",
    }
    if time_min:
        kwargs["timeMin"] = time_min
    if time_max:
        kwargs["timeMax"] = time_max
    result = service.events().list(**kwargs).execute()
    return {
        "calendar_id": calendar_id,
        "count": len(result.get("items", [])),
        "events": [
            {
                "id": e["id"],
                "summary": e.get("summary", ""),
                "start": e.get("start", {}),
                "end": e.get("end", {}),
                "location": e.get("location", ""),
                "attendees": e.get("attendees", []),
            }
            for e in result.get("items", [])
        ],
    }


async def _calendar_create_event(args: dict[str, Any]) -> dict[str, Any]:
    summary = str(args["summary"])
    start = args["start"]
    end = args["end"]
    description = str(args.get("description", ""))
    calendar_id = str(args.get("calendar_id", "primary"))
    service = _build_service("calendar", "v3")
    event = {
        "summary": summary,
        "description": description,
        "start": start if isinstance(start, dict) else {"dateTime": start},
        "end": end if isinstance(end, dict) else {"dateTime": end},
    }
    created = service.events().insert(calendarId=calendar_id, body=event).execute()
    return {
        "id": created.get("id"),
        "html_link": created.get("htmlLink"),
        "summary": summary,
        "created": True,
    }


def tools() -> list[ToolDescriptor]:
    return [
        ToolDescriptor(
            name="gmail.list_threads",
            description="List Gmail threads, optionally filtered by a Gmail search query.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 100},
                },
            },
            handler=_gmail_list_threads,
            annotations={"readOnlyHint": True, "openWorldHint": True, "idempotentHint": True},
        ),
        ToolDescriptor(
            name="gmail.read_thread",
            description="Read full content of a Gmail thread by id (headers + body).",
            input_schema={
                "type": "object",
                "properties": {"thread_id": {"type": "string"}},
                "required": ["thread_id"],
            },
            handler=_gmail_read_thread,
            annotations={"readOnlyHint": True, "openWorldHint": True, "idempotentHint": True},
        ),
        ToolDescriptor(
            name="gmail.send",
            description="Send a Gmail message. IRREVERSIBLE social commitment.",
            input_schema={
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["to", "subject", "body"],
            },
            handler=_gmail_send,
            annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True},
        ),
        ToolDescriptor(
            name="gmail.search",
            description="Search Gmail with a query string (same as list_threads with query).",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "required": ["query"],
            },
            handler=_gmail_search,
            annotations={"readOnlyHint": True, "openWorldHint": True, "idempotentHint": True},
        ),
        ToolDescriptor(
            name="docs.read",
            description="Read the plain-text content of a Google Doc by document_id.",
            input_schema={
                "type": "object",
                "properties": {"document_id": {"type": "string"}},
                "required": ["document_id"],
            },
            handler=_docs_read,
            annotations={"readOnlyHint": True, "openWorldHint": True, "idempotentHint": True},
        ),
        ToolDescriptor(
            name="docs.create",
            description="Create a new Google Doc with optional initial body text.",
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["title"],
            },
            handler=_docs_create,
            annotations={"readOnlyHint": False, "openWorldHint": True},
        ),
        ToolDescriptor(
            name="drive.list",
            description="List Google Drive files, optionally filtered by a Drive search query.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 100},
                },
            },
            handler=_drive_list,
            annotations={"readOnlyHint": True, "openWorldHint": True, "idempotentHint": True},
        ),
        ToolDescriptor(
            name="drive.read_file_content",
            description="Read text content of a Drive file (Docs/Sheets exported as text/CSV; raw text otherwise).",
            input_schema={
                "type": "object",
                "properties": {"file_id": {"type": "string"}},
                "required": ["file_id"],
            },
            handler=_drive_read_file_content,
            annotations={"readOnlyHint": True, "openWorldHint": True, "idempotentHint": True},
        ),
        ToolDescriptor(
            name="calendar.list_events",
            description="List Google Calendar events in a time range (RFC3339 timestamps).",
            input_schema={
                "type": "object",
                "properties": {
                    "time_min": {"type": "string"},
                    "time_max": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 100},
                    "calendar_id": {"type": "string"},
                },
            },
            handler=_calendar_list_events,
            annotations={"readOnlyHint": True, "openWorldHint": True, "idempotentHint": True},
        ),
        ToolDescriptor(
            name="calendar.create_event",
            description="Create a Google Calendar event. start and end are RFC3339 timestamps or {dateTime, timeZone} objects.",
            input_schema={
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "start": {},
                    "end": {},
                    "description": {"type": "string"},
                    "calendar_id": {"type": "string"},
                },
                "required": ["summary", "start", "end"],
            },
            handler=_calendar_create_event,
            annotations={"readOnlyHint": False, "openWorldHint": True},
        ),
    ]


async def serve() -> None:
    await serve_tools(SERVER_NAME, tools())


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
