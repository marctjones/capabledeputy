"""Bundled MCP server: IMAP / SMTP mail (no OAuth required).

For operators who want Gmail (or any IMAP server) without registering
an OAuth client in Google Cloud Console. Uses stdlib `imaplib` +
`smtplib`. Works with any IMAP+SMTP server: Gmail, Fastmail,
ProtonMail Bridge, self-hosted Postfix, Outlook (where legacy auth
is enabled), etc.

For Gmail specifically: operator generates a 16-character App Password
at https://myaccount.google.com/apppasswords and saves it to a file.

Tools shipped:
  imap.list_threads(folder="INBOX", query="", max_results=20)
  imap.read_message(uid)
  imap.search(query, max_results=20)
  imap.send(to, subject, body, from_address=None)
  imap.list_folders()
  imap.mark_read(uid)
  imap.archive(uid)

For Gmail, `query` accepts Gmail search syntax via the X-GM-RAW
extension (e.g., "is:unread", "from:alice@example.com", "after:2026/05/01").
For other servers, `query` falls back to standard IMAP SEARCH terms.

Run via:
  capdep mcp-server-imap
  python -m capabledeputy.mcp_servers.imap
"""

from __future__ import annotations

import asyncio
import contextlib
import email
import email.message
import email.utils
import imaplib
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from capabledeputy.mcp_servers._common import ToolDescriptor, serve_tools
from capabledeputy.mcp_servers._imap_creds import load_config

SERVER_NAME = "capdep-imap"


def _connect_imap() -> imaplib.IMAP4_SSL:
    """Open an IMAP4-SSL connection + login using operator config.

    Caller is responsible for `client.logout()` when done. Errors
    (auth, network) bubble; the caller wraps them in a tool result.
    """
    cfg = load_config().imap
    client = imaplib.IMAP4_SSL(cfg.host, cfg.port, ssl_context=ssl.create_default_context())
    client.login(cfg.username, cfg.password)
    return client


def _is_gmail(host: str) -> bool:
    return "gmail" in host.lower() or "googlemail" in host.lower()


def _parse_addresses(header: str | None) -> str:
    if not header:
        return ""
    return str(header)


# ---------- imap.list_threads ----------


async def _list_threads(args: dict[str, Any]) -> dict[str, Any]:
    folder = str(args.get("folder", "INBOX"))
    query = str(args.get("query", ""))
    max_results = int(args.get("max_results", 20))
    client = _connect_imap()
    try:
        client.select(folder)
        is_gmail = _is_gmail(load_config().imap.host)
        if query and is_gmail:
            # Gmail X-GM-RAW lets us use Gmail's search syntax verbatim
            typ, data = client.uid("SEARCH", "X-GM-RAW", f'"{query}"')
        elif query:
            # imaplib.uid accepts None as the optional charset arg (no charset);
            # imaplib stubs disagree, hence the type: ignore.
            typ, data = client.uid("SEARCH", None, query)  # type: ignore[arg-type]
        else:
            typ, data = client.uid("SEARCH", None, "ALL")  # type: ignore[arg-type]
        if typ != "OK":
            return {"folder": folder, "query": query, "count": 0, "messages": []}
        uids = data[0].split() if data and data[0] else []
        # Most recent first
        uids = list(reversed(uids))[:max_results]
        messages = []
        for uid_bytes in uids:
            uid = uid_bytes.decode("ascii")
            typ, hdr_data = client.uid(
                "FETCH",
                uid,
                "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID)])",
            )
            if typ != "OK" or not hdr_data:
                continue
            for item in hdr_data:
                if not isinstance(item, tuple) or len(item) < 2:
                    continue
                raw = item[1]
                if isinstance(raw, bytes):
                    msg = email.message_from_bytes(raw)
                    messages.append(
                        {
                            "uid": uid,
                            "from": _parse_addresses(msg.get("From")),
                            "to": _parse_addresses(msg.get("To")),
                            "subject": msg.get("Subject", ""),
                            "date": msg.get("Date", ""),
                            "message_id": msg.get("Message-ID", ""),
                        },
                    )
        return {
            "folder": folder,
            "query": query,
            "count": len(messages),
            "messages": messages,
        }
    finally:
        with contextlib.suppress(Exception):
            client.logout()


# ---------- imap.read_message ----------


async def _read_message(args: dict[str, Any]) -> dict[str, Any]:
    uid = str(args["uid"])
    folder = str(args.get("folder", "INBOX"))
    client = _connect_imap()
    try:
        client.select(folder)
        typ, data = client.uid("FETCH", uid, "(RFC822)")
        if typ != "OK" or not data:
            return {"found": False, "uid": uid, "folder": folder}
        for item in data:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            raw = item[1]
            if not isinstance(raw, bytes):
                continue
            msg = email.message_from_bytes(raw)
            body = _extract_body(msg)
            return {
                "found": True,
                "uid": uid,
                "folder": folder,
                "from": _parse_addresses(msg.get("From")),
                "to": _parse_addresses(msg.get("To")),
                "cc": _parse_addresses(msg.get("Cc")),
                "subject": msg.get("Subject", ""),
                "date": msg.get("Date", ""),
                "message_id": msg.get("Message-ID", ""),
                "body": body[:8192],
            }
        return {"found": False, "uid": uid, "folder": folder}
    finally:
        with contextlib.suppress(Exception):
            client.logout()


def _extract_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain":
                try:
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        charset = part.get_content_charset() or "utf-8"
                        return payload.decode(charset, errors="replace")
                except Exception:
                    continue
        # Fallback to first text/* if no text/plain
        for part in msg.walk():
            if part.get_content_maintype() == "text":
                try:
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        charset = part.get_content_charset() or "utf-8"
                        return payload.decode(charset, errors="replace")
                except Exception:
                    continue
        return ""
    try:
        payload = msg.get_payload(decode=True)
        if isinstance(payload, bytes):
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
        return str(payload or "")
    except Exception:
        return ""


# ---------- imap.search ----------


async def _search(args: dict[str, Any]) -> dict[str, Any]:
    # Search is list_threads with a query — same call, distinct tool
    # for clarity / discoverability to the LLM.
    return await _list_threads(args)


# ---------- imap.send (SMTP) ----------


async def _send(args: dict[str, Any]) -> dict[str, Any]:
    to_address = str(args["to"])
    subject = str(args["subject"])
    body = str(args["body"])
    cfg = load_config().smtp
    from_address = str(args.get("from_address") or cfg.username)

    msg = MIMEMultipart()
    msg["From"] = from_address
    msg["To"] = to_address
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid()
    msg.attach(MIMEText(body, "plain", "utf-8"))

    # 465 = SMTPS (implicit TLS); 587 = STARTTLS
    if cfg.port == 465:
        with smtplib.SMTP_SSL(
            cfg.host,
            cfg.port,
            context=ssl.create_default_context(),
        ) as smtp:
            smtp.login(cfg.username, cfg.password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(cfg.host, cfg.port) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(cfg.username, cfg.password)
            smtp.send_message(msg)

    return {
        "sent": True,
        "to": to_address,
        "subject": subject,
        "from": from_address,
        "message_id": msg["Message-ID"],
    }


# ---------- imap.list_folders ----------


async def _list_folders(args: dict[str, Any]) -> dict[str, Any]:
    client = _connect_imap()
    try:
        typ, data = client.list()
        folders: list[str] = []
        if typ == "OK" and data:
            for entry in data:
                if entry is None:
                    continue
                line = (
                    entry.decode("utf-8", errors="replace")
                    if isinstance(entry, bytes)
                    else str(entry)
                )
                # Typical format: (\HasNoChildren) "/" "INBOX"
                parts = line.split('"')
                if len(parts) >= 3:
                    folders.append(parts[-2])
        return {"count": len(folders), "folders": folders}
    finally:
        with contextlib.suppress(Exception):
            client.logout()


# ---------- imap.mark_read ----------


async def _mark_read(args: dict[str, Any]) -> dict[str, Any]:
    uid = str(args["uid"])
    folder = str(args.get("folder", "INBOX"))
    client = _connect_imap()
    try:
        client.select(folder)
        typ, _ = client.uid("STORE", uid, "+FLAGS", "(\\Seen)")
        return {"uid": uid, "folder": folder, "marked_read": typ == "OK"}
    finally:
        with contextlib.suppress(Exception):
            client.logout()


# ---------- imap.archive ----------


async def _archive(args: dict[str, Any]) -> dict[str, Any]:
    """Gmail: remove the \\Inbox label (X-GM-LABELS). Other IMAP: move
    to "[Gmail]/All Mail" if present, else "Archive" if present, else
    error.
    """
    uid = str(args["uid"])
    folder = str(args.get("folder", "INBOX"))
    client = _connect_imap()
    try:
        client.select(folder)
        cfg = load_config().imap
        if _is_gmail(cfg.host):
            # Gmail-specific: drop the \\Inbox label
            typ, _ = client.uid("STORE", uid, "-X-GM-LABELS", "\\Inbox")
            return {"uid": uid, "archived": typ == "OK", "backend": "gmail-labels"}
        # Generic IMAP: try MOVE to common archive folders
        for target in ("Archive", "[Gmail]/All Mail", "ARCHIVE"):
            try:
                typ, _ = client.uid("MOVE", uid, target)
                if typ == "OK":
                    return {
                        "uid": uid,
                        "archived": True,
                        "backend": "imap-move",
                        "target_folder": target,
                    }
            except Exception:
                continue
        return {
            "uid": uid,
            "archived": False,
            "error": "no archive folder found; tried Archive / [Gmail]/All Mail / ARCHIVE",
        }
    finally:
        with contextlib.suppress(Exception):
            client.logout()


def tools() -> list[ToolDescriptor]:
    return [
        ToolDescriptor(
            name="imap.list_threads",
            description=(
                "List messages in an IMAP folder, optionally filtered by a "
                "search query. For Gmail, query accepts Gmail search syntax "
                "(is:unread, from:..., after:...). For other servers, query "
                "uses standard IMAP SEARCH terms."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "folder": {"type": "string", "default": "INBOX"},
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 100},
                },
            },
            handler=_list_threads,
            annotations={"readOnlyHint": True, "openWorldHint": True, "idempotentHint": True},
        ),
        ToolDescriptor(
            name="imap.read_message",
            description="Read the full content of a single message by UID + folder.",
            input_schema={
                "type": "object",
                "properties": {
                    "uid": {"type": "string"},
                    "folder": {"type": "string", "default": "INBOX"},
                },
                "required": ["uid"],
            },
            handler=_read_message,
            annotations={"readOnlyHint": True, "openWorldHint": True, "idempotentHint": True},
        ),
        ToolDescriptor(
            name="imap.search",
            description=(
                "Search messages with an IMAP / Gmail query. Same as "
                "list_threads but with `query` required."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "folder": {"type": "string", "default": "INBOX"},
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "required": ["query"],
            },
            handler=_search,
            annotations={"readOnlyHint": True, "openWorldHint": True, "idempotentHint": True},
        ),
        ToolDescriptor(
            name="imap.send",
            description=(
                "Send an email via SMTP. IRREVERSIBLE social commitment. "
                "from_address defaults to the operator-configured SMTP username."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                    "from_address": {"type": "string"},
                },
                "required": ["to", "subject", "body"],
            },
            handler=_send,
            annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True},
        ),
        ToolDescriptor(
            name="imap.list_folders",
            description="List all IMAP folders / labels.",
            input_schema={"type": "object", "properties": {}},
            handler=_list_folders,
            annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
        ),
        ToolDescriptor(
            name="imap.mark_read",
            description="Mark a message as read by UID.",
            input_schema={
                "type": "object",
                "properties": {
                    "uid": {"type": "string"},
                    "folder": {"type": "string", "default": "INBOX"},
                },
                "required": ["uid"],
            },
            handler=_mark_read,
            annotations={"readOnlyHint": False, "openWorldHint": False, "idempotentHint": True},
        ),
        ToolDescriptor(
            name="imap.archive",
            description=(
                "Archive a message. Gmail: removes the \\Inbox label via "
                "X-GM-LABELS. Other IMAP servers: moves to Archive / "
                "[Gmail]/All Mail / ARCHIVE if present."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "uid": {"type": "string"},
                    "folder": {"type": "string", "default": "INBOX"},
                },
                "required": ["uid"],
            },
            handler=_archive,
            annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False},
        ),
    ]


async def serve() -> None:
    await serve_tools(SERVER_NAME, tools())


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
