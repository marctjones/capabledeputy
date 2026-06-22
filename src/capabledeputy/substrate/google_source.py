"""Google Workspace `SourcePort` providers.

These adapters canonicalize Google resource identifiers without reaching
out to Google APIs. They intentionally accept only stable IDs or URL
forms that expose stable IDs; ambiguous search terms fail closed.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse

from capabledeputy.substrate.source_port import SourcePort


class GoogleSourcePortError(RuntimeError):
    """Fail-closed Google canonicalization failure."""


_STABLE_ID = re.compile(r"^[A-Za-z0-9_.@<>=+-][A-Za-z0-9_.@<>=+:/-]{1,512}$")
_DRIVE_DOC_RE = re.compile(r"^/(?:file|document|spreadsheets|presentation|forms)/d/([^/]+)")


def _require_stable_id(value: str, *, kind: str) -> str:
    raw = unquote(value).strip()
    if not raw or not _STABLE_ID.match(raw):
        raise GoogleSourcePortError(f"cannot canonicalize {kind} id from {value!r}")
    return raw


def _maybe_decode_calendar_eid(eid: str) -> str:
    """Google Calendar event URLs often carry base64-ish `eid` values.

    If decoding yields a plausible event id, use it; otherwise keep the
    URL-visible token because it is still stable enough for audit identity.
    """
    token = eid.strip()
    padded = token + ("=" * (-len(token) % 4))
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except Exception:
        return token
    first = decoded.split()[0].strip()
    return first or token


@dataclass(frozen=True)
class GmailSourcePort(SourcePort):
    """Canonical Gmail message/thread identifiers."""

    surfaces_destination_id: bool = True

    def canonicalize_resource(self, uri: str) -> str:
        return f"gmail:message:{self._message_id(uri)}"

    def canonical_destination_id(self, target: str) -> str:
        raw = target.strip()
        if raw.startswith("mailto:"):
            raw = raw[len("mailto:") :]
        if "@" in raw and not raw.startswith(("gmail:", "https://", "http://")):
            return f"gmail:recipient:{raw.lower()}"
        return self.canonicalize_resource(target)

    def _message_id(self, uri: str) -> str:
        raw = uri.strip()
        for prefix in ("gmail:message:", "gmail://message/", "gmail:"):
            if raw.startswith(prefix):
                return _require_stable_id(raw[len(prefix) :], kind="gmail message")
        if raw.startswith(("http://", "https://")):
            parsed = urlparse(raw)
            if parsed.netloc != "mail.google.com":
                raise GoogleSourcePortError(f"not a Gmail URL: {uri!r}")
            fragment = parsed.fragment.strip("/")
            if fragment:
                candidate = fragment.rsplit("/", 1)[-1]
                if candidate and candidate not in {"inbox", "sent", "all"}:
                    return _require_stable_id(candidate, kind="gmail message")
            query = parse_qs(parsed.query)
            for key in ("th", "message_id", "msgid"):
                if query.get(key):
                    return _require_stable_id(query[key][0], kind="gmail message")
            raise GoogleSourcePortError(f"Gmail URL does not expose a stable message id: {uri!r}")
        if raw.startswith("<") and raw.endswith(">"):
            return _require_stable_id(raw, kind="gmail rfc822 message-id")
        return _require_stable_id(raw, kind="gmail message")


@dataclass(frozen=True)
class GoogleDriveSourcePort(SourcePort):
    """Canonical Google Drive file identifiers."""

    surfaces_destination_id: bool = True

    def canonicalize_resource(self, uri: str) -> str:
        return f"google-drive:file:{self._file_id(uri)}"

    def canonical_destination_id(self, target: str) -> str:
        return self.canonicalize_resource(target)

    def _file_id(self, uri: str) -> str:
        raw = uri.strip()
        for prefix in ("google-drive:file:", "drive:file:", "gdrive:", "drive:"):
            if raw.startswith(prefix):
                return _require_stable_id(raw[len(prefix) :], kind="drive file")
        if raw.startswith(("http://", "https://")):
            parsed = urlparse(raw)
            if parsed.netloc not in {"drive.google.com", "docs.google.com"}:
                raise GoogleSourcePortError(f"not a Google Drive URL: {uri!r}")
            match = _DRIVE_DOC_RE.match(parsed.path)
            if match:
                return _require_stable_id(match.group(1), kind="drive file")
            query = parse_qs(parsed.query)
            if query.get("id"):
                return _require_stable_id(query["id"][0], kind="drive file")
            raise GoogleSourcePortError(f"Drive URL does not expose a stable file id: {uri!r}")
        return _require_stable_id(raw, kind="drive file")


@dataclass(frozen=True)
class GoogleCalendarSourcePort(SourcePort):
    """Canonical Google Calendar event identifiers."""

    surfaces_destination_id: bool = True

    def canonicalize_resource(self, uri: str) -> str:
        return f"google-calendar:event:{self._event_id(uri)}"

    def canonical_destination_id(self, target: str) -> str:
        return self.canonicalize_resource(target)

    def _event_id(self, uri: str) -> str:
        raw = uri.strip()
        for prefix in ("google-calendar:event:", "calendar:event:", "calendar:"):
            if raw.startswith(prefix):
                return _require_stable_id(raw[len(prefix) :], kind="calendar event")
        if raw.startswith(("http://", "https://")):
            parsed = urlparse(raw)
            if parsed.netloc not in {"calendar.google.com", "www.google.com"}:
                raise GoogleSourcePortError(f"not a Google Calendar URL: {uri!r}")
            query = parse_qs(parsed.query)
            for key in ("event_id", "eventid"):
                if query.get(key):
                    return _require_stable_id(query[key][0], kind="calendar event")
            if query.get("eid"):
                return _require_stable_id(
                    _maybe_decode_calendar_eid(query["eid"][0]),
                    kind="calendar event",
                )
            raise GoogleSourcePortError(f"Calendar URL does not expose a stable event id: {uri!r}")
        return _require_stable_id(raw, kind="calendar event")
