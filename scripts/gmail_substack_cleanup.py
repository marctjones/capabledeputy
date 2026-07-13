#!/usr/bin/env python3
"""DEV-ONLY: Gmail Substack hygiene outside CapDep policy.

Creates a Gmail filter, bulk-archives via IMAP, and POSTs List-Unsubscribe
URLs. This bypasses CapDep approvals and capability grants — use only for
one-off operator maintenance, not as a product workflow.

Uses the operator's Google OAuth token plus IMAP credentials from
``~/.config/capabledeputy/``.
"""

from __future__ import annotations

import argparse
import email
import imaplib
import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SUBSTACK_FILTER_QUERY = "from:substack.com"
SUBSTACK_FILTER_ID_FILE = Path.home() / ".config/capabledeputy/gmail-substack-filter.json"


def _load_google_access_token() -> str:
    token_path = Path.home() / ".config/capabledeputy/oauth/google-gmail.json"
    data = json.loads(token_path.read_text(encoding="utf-8"))
    return str(data["access_token"])


def _gmail_api(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"https://gmail.googleapis.com/gmail/v1/users/me{path}"
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=payload,
        method=method,
        headers={
            "Authorization": f"Bearer {_load_google_access_token()}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gmail API {method} {path} failed ({exc.code}): {detail}") from exc


def list_filters() -> list[dict[str, Any]]:
    return _gmail_api("GET", "/settings/filters").get("filter", []) or []


def create_substack_archive_filter() -> dict[str, Any]:
    for existing in list_filters():
        criteria = existing.get("criteria", {})
        if criteria.get("query") == SUBSTACK_FILTER_QUERY:
            return {"created": False, "filter": existing, "reason": "already_exists"}

    created = _gmail_api(
        "POST",
        "/settings/filters",
        {
            "criteria": {"query": SUBSTACK_FILTER_QUERY},
            "action": {
                "removeLabelIds": ["INBOX"],
                "addLabelIds": [],
            },
        },
    )
    SUBSTACK_FILTER_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
    SUBSTACK_FILTER_ID_FILE.write_text(json.dumps(created, indent=2) + "\n", encoding="utf-8")
    return {"created": True, "filter": created}


def _parse_unsubscribe_headers(header_blob: str) -> tuple[str | None, str | None]:
    msg = email.message_from_string(header_blob)
    one_click = msg.get("List-Unsubscribe-Post", "")
    raw = msg.get("List-Unsubscribe", "")
    if not raw:
        return None, None
    urls = re.findall(r"<([^>]+)>", raw)
    http_url = next((u for u in urls if u.startswith("http")), None)
    mailto_url = next((u for u in urls if u.startswith("mailto:")), None)
    if "List-Unsubscribe=One-Click" in one_click and http_url:
        return http_url, "POST"
    return http_url or mailto_url, "GET" if http_url else "MAILTO"


def _imap_connect():
    from capabledeputy.mcp_servers._imap_creds import load_config

    cfg = load_config().imap
    client = imaplib.IMAP4_SSL(cfg.host, cfg.port)
    client.login(cfg.username, cfg.password)
    return client


def collect_substack_unsubscribe_targets(max_messages: int) -> list[dict[str, str]]:
    client = _imap_connect()
    try:
        client.select("INBOX", readonly=True)
        typ, data = client.uid("SEARCH", None, "FROM", "substack.com")
        if typ != "OK" or not data or not data[0]:
            return []
        uids = data[0].split()
        uids = uids[-max_messages:]
        seen: set[str] = set()
        targets: list[dict[str, str]] = []
        for uid in reversed(uids):
            typ, fetched = client.uid("FETCH", uid, "(BODY.PEEK[HEADER])")
            if typ != "OK" or not fetched:
                continue
            header_blob = ""
            for part in fetched:
                if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], bytes):
                    header_blob = part[1].decode("utf-8", errors="replace")
                    break
            if not header_blob:
                continue
            msg = email.message_from_string(header_blob)
            sender = msg.get("From", "")
            subject = msg.get("Subject", "")
            url, method = _parse_unsubscribe_headers(header_blob)
            if not url or url in seen:
                continue
            seen.add(url)
            targets.append(
                {
                    "uid": uid.decode() if isinstance(uid, bytes) else str(uid),
                    "sender": sender,
                    "subject": subject,
                    "url": url,
                    "method": method,
                },
            )
        return targets
    finally:
        with contextlib_suppress():
            client.logout()


def contextlib_suppress():
    import contextlib

    return contextlib.suppress(Exception)


def _unsubscribe_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }


def unsubscribe_url(target: dict[str, str]) -> tuple[bool, str]:
    url = target["url"]
    method = target["method"]
    if method == "MAILTO":
        return False, "mailto_only"
    try:
        headers = _unsubscribe_headers()
        if method == "POST":
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            req = urllib.request.Request(
                url,
                data=b"List-Unsubscribe=One-Click",
                method="POST",
                headers=headers,
            )
        else:
            req = urllib.request.Request(url, method="GET", headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            return True, f"http_{resp.status}"
    except urllib.error.HTTPError as exc:
        if exc.code in {200, 202, 204, 302, 303}:
            return True, f"http_{exc.code}"
        return False, f"http_{exc.code}"
    except Exception as exc:
        return False, str(exc)


def archive_substack_inbox(max_messages: int) -> dict[str, int]:
    client = _imap_connect()
    archived = 0
    failed = 0
    try:
        client.select("INBOX")
        typ, data = client.uid("SEARCH", None, "FROM", "substack.com")
        if typ != "OK" or not data or not data[0]:
            return {"archived": 0, "failed": 0}
        uids = data[0].split()
        if max_messages > 0:
            uids = uids[-max_messages:]
        for uid in uids:
            typ, _ = client.uid("STORE", uid, "-X-GM-LABELS", "\\Inbox")
            if typ == "OK":
                archived += 1
            else:
                failed += 1
        return {"archived": archived, "failed": failed}
    finally:
        with contextlib_suppress():
            client.logout()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-filter", action="store_true")
    parser.add_argument("--skip-unsubscribe", action="store_true")
    parser.add_argument("--skip-archive", action="store_true")
    parser.add_argument("--max-unsubscribe", type=int, default=40)
    parser.add_argument("--max-archive", type=int, default=500)
    args = parser.parse_args()

    if not args.skip_filter:
        result = create_substack_archive_filter()
        print("FILTER:", json.dumps(result, indent=2))

    if not args.skip_unsubscribe:
        targets = collect_substack_unsubscribe_targets(args.max_unsubscribe)
        print(f"UNSUBSCRIBE_TARGETS: {len(targets)} unique publications")
        ok = 0
        for target in targets:
            success, detail = unsubscribe_url(target)
            status = "ok" if success else "fail"
            sender = target["sender"][:60]
            print(f"  [{status}] {sender} -> {detail}")
            if success:
                ok += 1
        print(f"UNSUBSCRIBED: {ok}/{len(targets)}")

    if not args.skip_archive:
        stats = archive_substack_inbox(args.max_archive)
        print("ARCHIVED_EXISTING:", json.dumps(stats))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
