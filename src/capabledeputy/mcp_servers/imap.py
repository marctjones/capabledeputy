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
from datetime import date
from email.header import decode_header, make_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html.parser import HTMLParser
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
    try:
        client.login(cfg.username, cfg.password)
    except BaseException:
        # Authentication failed before the caller received ownership. Close the
        # socket here; no mailbox operation or implicit reconnect is permitted.
        with contextlib.suppress(Exception):
            client.shutdown()
        raise
    return client


def _is_gmail(host: str) -> bool:
    return "gmail" in host.lower() or "googlemail" in host.lower()


def _parse_addresses(header: str | None) -> str:
    if not header:
        return ""
    return str(make_header(decode_header(str(header))))


def _select_readonly(client: imaplib.IMAP4_SSL, folder: str) -> str | None:
    typ, _ = client.select(folder, readonly=True)
    if typ != "OK":
        raise ValueError("IMAP mailbox selection failed")
    _, values = client.response("UIDVALIDITY")
    if values and isinstance(values[0], bytes):
        return values[0].decode("ascii")
    return None


def _fetch_message(
    client: imaplib.IMAP4_SSL,
    uid: str,
    folder: str,
    body_limit: int = 8192,
) -> dict[str, Any]:
    typ, data = client.uid("FETCH", uid, "(BODY.PEEK[])")
    base: dict[str, Any] = {"uid": uid, "folder": folder, "found": False}
    if typ != "OK":
        return {**base, "ok": False, "error": "fetch_failed"}
    for item in data or []:
        if not isinstance(item, tuple) or len(item) < 2 or not isinstance(item[1], bytes):
            continue
        msg = email.message_from_bytes(item[1])
        body = _extract_body(msg)
        return {
            **base,
            "ok": True,
            "found": True,
            "from": _parse_addresses(msg.get("From")),
            "reply_to": _parse_addresses(msg.get("Reply-To")),
            "to": _parse_addresses(msg.get("To")),
            "cc": _parse_addresses(msg.get("Cc")),
            "subject": _parse_addresses(msg.get("Subject")),
            "date": msg.get("Date", ""),
            "message_id": msg.get("Message-ID", ""),
            "in_reply_to": msg.get("In-Reply-To", ""),
            "references": msg.get("References", ""),
            "body": body[:body_limit],
            "body_present": bool(body.strip()),
            "body_truncated": len(body) > body_limit,
        }
    return {**base, "ok": True, "error": "message_not_found"}


# ---------- imap.list_threads ----------


async def _list_threads(args: dict[str, Any]) -> dict[str, Any]:
    folder = str(args.get("folder", "INBOX"))
    query = str(args.get("query", ""))
    max_results = int(args.get("max_results", 20))
    if not 1 <= max_results <= 100:
        raise ValueError("max_results must be between 1 and 100")
    since = args.get("since_date")
    since_term = date.fromisoformat(str(since)).strftime("%d-%b-%Y") if since else None
    include_body = bool(args.get("include_body", False))
    client = _connect_imap()
    try:
        uidvalidity = _select_readonly(client, folder)
        is_gmail = _is_gmail(load_config().imap.host)
        if since_term and query:
            raise ValueError("Use either since_date or query, not both")
        if since_term:
            typ, data = client.uid("SEARCH", None, "SINCE", since_term)  # type: ignore[arg-type]
        elif query and is_gmail:
            # Gmail X-GM-RAW lets us use Gmail's search syntax verbatim
            typ, data = client.uid("SEARCH", "X-GM-RAW", f'"{query}"')
        elif query:
            # imaplib.uid accepts None as the optional charset arg (no charset);
            # imaplib stubs disagree, hence the type: ignore.
            typ, data = client.uid("SEARCH", None, query)  # type: ignore[arg-type]
        else:
            typ, data = client.uid("SEARCH", None, "ALL")  # type: ignore[arg-type]
        if typ != "OK":
            return {
                "ok": False,
                "error": "search_failed",
                "folder": folder,
                "query": query,
                "count": 0,
                "messages": [],
            }
        uids = data[0].split() if data and data[0] else []
        # Most recent first
        matched_count = len(uids)
        uids = list(reversed(uids))[:max_results]
        messages = []
        failures = []
        for uid_bytes in uids:
            uid = uid_bytes.decode("ascii")
            if include_body:
                result = _fetch_message(client, uid, folder, min(8192, 16384 // len(uids)))
                result["uidvalidity"] = uidvalidity
                if not result["found"] or not result["ok"]:
                    failures.append(result)
                else:
                    messages.append(result)
                continue
            typ, hdr_data = client.uid(
                "FETCH",
                uid,
                "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID)])",
            )
            if typ != "OK" or not hdr_data:
                failures.append({"uid": uid, "error": "header_fetch_failed"})
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
                            "folder": folder,
                            "uidvalidity": uidvalidity,
                            "from": _parse_addresses(msg.get("From")),
                            "to": _parse_addresses(msg.get("To")),
                            "subject": _parse_addresses(msg.get("Subject")),
                            "date": msg.get("Date", ""),
                            "message_id": msg.get("Message-ID", ""),
                        },
                    )
        return {
            "ok": not failures,
            "complete": not failures and matched_count <= max_results,
            "matched_count": matched_count,
            "requested_count": len(uids),
            "failed_count": len(failures),
            "failures": failures,
            "limit_reached": matched_count > max_results,
            "bodies_included": include_body,
            "uidvalidity": uidvalidity,
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
    if not uid.isascii() or not uid.isdigit() or int(uid) < 1:
        raise ValueError("uid must be a positive numeric message UID")
    folder = str(args.get("folder", "INBOX"))
    client = _connect_imap()
    try:
        uidvalidity = _select_readonly(client, folder)
        if args.get("uidvalidity") is not None and str(args["uidvalidity"]) != uidvalidity:
            return {"ok": False, "found": False, "error": "stale_mailbox_reference"}
        return {**_fetch_message(client, uid, folder), "uidvalidity": uidvalidity}
    finally:
        with contextlib.suppress(Exception):
            client.logout()


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"br", "p", "div", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def _extract_body(msg: email.message.Message) -> str:
    parts = [
        p
        for p in msg.walk()
        if p.get_content_disposition() != "attachment"
        and p.get_content_type() in {"text/plain", "text/html"}
    ]
    parts.sort(key=lambda p: p.get_content_type() != "text/plain")
    for part in parts:
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            continue
        try:
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")
        if part.get_content_type() == "text/html":
            parser = _HTMLText()
            parser.feed(text)
            text = "".join(parser.parts)
        if text.strip():
            return text
    return ""


async def _read_recent(args: dict[str, Any]) -> dict[str, Any]:
    """One bounded read operation; message IDs come from SEARCH, never the model."""
    limit = int(args.get("max_results", 10))
    if not 1 <= limit <= 20:
        raise ValueError("read_recent max_results must be between 1 and 20")
    return await _list_threads({**args, "max_results": limit, "include_body": True})


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
            name="imap.read_recent",
            description=(
                "Read and summarize real email: searches then fetches actual message bodies "
                "without guessing UIDs or marking messages seen. Use this for recent/today's "
                "email summaries instead of listing headers then inventing IDs. "
                "since_date is inclusive YYYY-MM-DD in the mailbox's date semantics; "
                "use either since_date or provider search query. Report incomplete coverage, "
                "failed reads, empty bodies and body_truncated; never treat them as success."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "folder": {"type": "string", "default": "INBOX"},
                    "query": {"type": "string"},
                    "since_date": {"type": "string", "format": "date"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
                },
            },
            handler=_read_recent,
            annotations={"read_only_hint": True, "open_world_hint": True, "idempotent_hint": True},
        ),
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
            annotations={"read_only_hint": True, "open_world_hint": True, "idempotent_hint": True},
        ),
        ToolDescriptor(
            name="imap.read_message",
            description="Read the full content of a single message by UID + folder.",
            input_schema={
                "type": "object",
                "properties": {
                    "uid": {"type": "string"},
                    "folder": {"type": "string", "default": "INBOX"},
                    "uidvalidity": {"type": "string"},
                },
                "required": ["uid"],
            },
            handler=_read_message,
            annotations={"read_only_hint": True, "open_world_hint": True, "idempotent_hint": True},
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
            annotations={"read_only_hint": True, "open_world_hint": True, "idempotent_hint": True},
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
            annotations={
                "read_only_hint": False,
                "destructive_hint": True,
                "open_world_hint": True,
            },
        ),
        ToolDescriptor(
            name="imap.list_folders",
            description="List all IMAP folders / labels.",
            input_schema={"type": "object", "properties": {}},
            handler=_list_folders,
            annotations={"read_only_hint": True, "idempotent_hint": True, "open_world_hint": False},
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
            annotations={
                "read_only_hint": False,
                "open_world_hint": False,
                "idempotent_hint": True,
            },
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
            annotations={
                "read_only_hint": False,
                "destructive_hint": True,
                "open_world_hint": False,
            },
        ),
    ]


async def serve() -> None:
    await serve_tools(SERVER_NAME, tools())


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
