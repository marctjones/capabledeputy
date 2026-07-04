"""Active-context SourcePorts for desktop and browser state.

These ports turn "whatever is currently visible" into canonical resource ids
before the agent sees or acts on it. Ambiguous or stale context fails closed:
clients can present the failure and ask the operator to re-select context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

from capabledeputy.policy.labels import LabelState, ProvenanceLevel, ProvenanceTag
from capabledeputy.substrate.source_port import SourcePort


class ActiveContextError(ValueError):
    """Fail-closed active-context import or canonicalization failure."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _labels_for_browser() -> LabelState:
    return LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)}))


def _labels_for_macos_app() -> LabelState:
    return LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.SYSTEM_INTERNAL)}))


def _labels_for_untrusted_app_content() -> LabelState:
    return LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)}))


@dataclass(frozen=True)
class ActiveContextRecord:
    source_kind: str
    uri: str
    canonical_id: str
    title: str = ""
    labels: LabelState = field(default_factory=LabelState)
    captured_at: datetime = field(default_factory=_utcnow)
    stale_after_seconds: int = 300
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_stale(self, now: datetime | None = None) -> bool:
        if self.stale_after_seconds <= 0:
            return False
        reference = _ensure_aware(now or _utcnow())
        return reference >= _ensure_aware(self.captured_at) + timedelta(
            seconds=self.stale_after_seconds,
        )

    def ensure_fresh(self, now: datetime | None = None) -> None:
        if self.is_stale(now):
            raise ActiveContextError(f"active context is stale: {self.canonical_id}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_kind": self.source_kind,
            "uri": self.uri,
            "canonical_id": self.canonical_id,
            "title": self.title,
            "labels": self.labels.to_dict(),
            "captured_at": _ensure_aware(self.captured_at).isoformat(),
            "stale_after_seconds": self.stale_after_seconds,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActiveContextRecord:
        captured_raw = str(data.get("captured_at") or "")
        captured = datetime.fromisoformat(captured_raw) if captured_raw else _utcnow()
        labels = LabelState.from_dict(data.get("labels") if isinstance(data, dict) else None)
        metadata = data.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        return cls(
            source_kind=str(data.get("source_kind") or ""),
            uri=str(data.get("uri") or ""),
            canonical_id=str(data.get("canonical_id") or ""),
            title=str(data.get("title") or ""),
            labels=labels,
            captured_at=_ensure_aware(captured),
            stale_after_seconds=int(data.get("stale_after_seconds") or 300),
            metadata={str(k): v for k, v in metadata.items()},
        )


class BrowserCurrentPageSourcePort(SourcePort):
    """Canonicalize the browser's current page as untrusted external input."""

    surfaces_destination_id = True

    def canonicalize_resource(self, uri: str) -> str:
        return f"browser:url:{_canonical_http_url(uri)}"

    def canonical_destination_id(self, target: str) -> str:
        return self.canonicalize_resource(target)

    def context_from_payload(self, payload: dict[str, Any]) -> ActiveContextRecord:
        uri = str(payload.get("url") or payload.get("uri") or "").strip()
        if not uri:
            raise ActiveContextError("browser context missing url")
        captured = _coerce_captured_at(payload.get("captured_at"))
        record = ActiveContextRecord(
            source_kind="browser.current-page",
            uri=uri,
            canonical_id=self.canonicalize_resource(uri),
            title=str(payload.get("title") or "").strip(),
            labels=_labels_for_browser(),
            captured_at=captured,
            stale_after_seconds=int(payload.get("stale_after_seconds") or 300),
            metadata=_metadata_without(payload, {"url", "uri", "title", "captured_at"}),
        )
        record.ensure_fresh()
        return record


class MacOSAppContextSourcePort(SourcePort):
    """Canonicalize frontmost-app resources from trusted native clients."""

    surfaces_destination_id = True

    _APP_SCHEME_ALLOWLIST: frozenset[str] = frozenset(
        {
            "file",
            "mailto",
            "message",
            "apple-mail",
            "calendar",
            "x-apple-calevent",
            "pages",
            "numbers",
            "keynote",
            "outlook",
            "word",
            "powerpoint",
        },
    )

    def canonicalize_resource(self, uri: str) -> str:
        raw = str(uri or "").strip()
        if not raw:
            raise ActiveContextError("macOS app context missing uri")
        parsed = urlparse(raw)
        scheme = parsed.scheme.lower()
        if scheme not in self._APP_SCHEME_ALLOWLIST:
            raise ActiveContextError(
                f"unsupported macOS active-context scheme: {scheme or '<none>'}",
            )
        if scheme == "file":
            return f"macos:file:{_canonical_file_uri(raw)}"
        token = _stable_scheme_payload(parsed)
        return f"macos:{scheme}:{token}"

    def canonical_destination_id(self, target: str) -> str:
        return self.canonicalize_resource(target)

    def context_from_payload(self, payload: dict[str, Any]) -> ActiveContextRecord:
        uri = str(payload.get("uri") or payload.get("url") or "").strip()
        if not uri:
            raise ActiveContextError("macOS context missing uri")
        captured = _coerce_captured_at(payload.get("captured_at"))
        app_bundle_id = str(payload.get("app_bundle_id") or "").strip()
        record = ActiveContextRecord(
            source_kind="macos.frontmost-app",
            uri=uri,
            canonical_id=self.canonicalize_resource(uri),
            title=str(payload.get("title") or payload.get("app_name") or "").strip(),
            labels=_labels_for_macos_app(),
            captured_at=captured,
            stale_after_seconds=int(payload.get("stale_after_seconds") or 300),
            metadata={
                **_metadata_without(
                    payload,
                    {"uri", "url", "title", "captured_at", "stale_after_seconds"},
                ),
                "app_bundle_id": app_bundle_id,
            },
        )
        record.ensure_fresh()
        return record


class _MacOSSpecificAppSourcePort(SourcePort):
    """Narrow SourcePort base for app-specific macOS active context."""

    source_kind: str = ""
    labels: LabelState = LabelState()

    def canonical_destination_id(self, target: str) -> str:
        return self.canonicalize_resource(target)

    def context_from_payload(self, payload: dict[str, Any]) -> ActiveContextRecord:
        uri = str(payload.get("uri") or payload.get("url") or payload.get("resource") or "").strip()
        if not uri:
            raise ActiveContextError(f"{self.source_kind} context missing uri")
        captured = _coerce_captured_at(payload.get("captured_at"))
        record = ActiveContextRecord(
            source_kind=self.source_kind,
            uri=uri,
            canonical_id=self.canonicalize_resource(uri),
            title=str(payload.get("title") or payload.get("name") or "").strip(),
            labels=self.labels,
            captured_at=captured,
            stale_after_seconds=int(payload.get("stale_after_seconds") or 300),
            metadata=_metadata_without(
                payload,
                {"uri", "url", "resource", "title", "name", "captured_at", "stale_after_seconds"},
            ),
        )
        record.ensure_fresh()
        return record


class AppleMailContextSourcePort(_MacOSSpecificAppSourcePort):
    """Canonical Apple Mail message/thread context."""

    source_kind = "apple-mail"
    labels = _labels_for_untrusted_app_content()

    def canonicalize_resource(self, uri: str) -> str:
        raw = str(uri or "").strip()
        if raw.startswith(("message://", "apple-mail://message/")):
            parsed = urlparse(raw)
            token = (parsed.netloc + parsed.path).strip("/")
            return f"macos:apple-mail:message:{_stable_token(token, kind='Apple Mail message')}"
        if raw.startswith("mailto:"):
            parsed = urlparse(raw)
            recipient = _stable_token(parsed.path, kind="Mail recipient").lower()
            return f"macos:apple-mail:recipient:{recipient}"
        if raw.startswith("<") and raw.endswith(">"):
            return f"macos:apple-mail:message:{_stable_token(raw, kind='RFC822 Message-ID')}"
        if raw.startswith("apple-mail:message:"):
            token = raw.removeprefix("apple-mail:message:")
            return f"macos:apple-mail:message:{_stable_token(token, kind='Apple Mail message')}"
        raise ActiveContextError(
            "Apple Mail context requires message://, apple-mail://message/, "
            "mailto:, or RFC822 Message-ID",
        )


class FinderContextSourcePort(_MacOSSpecificAppSourcePort):
    """Canonical Finder file or folder selection context."""

    source_kind = "finder"
    labels = _labels_for_macos_app()

    def canonicalize_resource(self, uri: str) -> str:
        raw = str(uri or "").strip()
        if not raw.startswith("file:"):
            raise ActiveContextError("Finder context requires an absolute file:// URI")
        return f"macos:finder:file:{_canonical_file_uri(raw)}"


class PagesContextSourcePort(_MacOSSpecificAppSourcePort):
    source_kind = "pages"
    labels = _labels_for_macos_app()

    def canonicalize_resource(self, uri: str) -> str:
        return _canonical_iwork_resource("pages", uri)


class NumbersContextSourcePort(_MacOSSpecificAppSourcePort):
    source_kind = "numbers"
    labels = _labels_for_macos_app()

    def canonicalize_resource(self, uri: str) -> str:
        return _canonical_iwork_resource("numbers", uri)


class KeynoteContextSourcePort(_MacOSSpecificAppSourcePort):
    source_kind = "keynote"
    labels = _labels_for_macos_app()

    def canonicalize_resource(self, uri: str) -> str:
        return _canonical_iwork_resource("keynote", uri)


class OutlookContextSourcePort(_MacOSSpecificAppSourcePort):
    source_kind = "outlook"
    labels = _labels_for_untrusted_app_content()

    def canonicalize_resource(self, uri: str) -> str:
        return _canonical_office_resource("outlook", uri)


class WordContextSourcePort(_MacOSSpecificAppSourcePort):
    source_kind = "word"
    labels = _labels_for_macos_app()

    def canonicalize_resource(self, uri: str) -> str:
        return _canonical_office_resource("word", uri)


class PowerPointContextSourcePort(_MacOSSpecificAppSourcePort):
    source_kind = "powerpoint"
    labels = _labels_for_macos_app()

    def canonicalize_resource(self, uri: str) -> str:
        return _canonical_office_resource("powerpoint", uri)


class CalendarContextSourcePort(_MacOSSpecificAppSourcePort):
    source_kind = "calendar"
    labels = _labels_for_macos_app()

    def canonicalize_resource(self, uri: str) -> str:
        raw = str(uri or "").strip()
        if raw.startswith("calendar:event:"):
            token = raw.removeprefix("calendar:event:")
            return f"macos:calendar:event:{_stable_token(token, kind='Calendar event')}"
        parsed = urlparse(raw)
        scheme = parsed.scheme.lower()
        if scheme in {"calendar", "x-apple-calevent", "ical"}:
            token = (parsed.netloc + parsed.path).strip("/")
            if not token:
                query = dict(parse_qsl(parsed.query, keep_blank_values=True))
                token = query.get("event_id") or query.get("uid") or ""
            return f"macos:calendar:event:{_stable_token(token, kind='Calendar event')}"
        raise ActiveContextError(
            "Calendar context requires calendar:, x-apple-calevent:, ical:, or calendar:event: URI",
        )


def active_context_from_payload(kind: str, payload: dict[str, Any]) -> ActiveContextRecord:
    """Import active context from a client payload, fail-closed on ambiguity."""

    normalized = kind.lower().replace("_", "-")
    if normalized in {"browser", "browser.current-page", "browser-current-page"}:
        return BrowserCurrentPageSourcePort().context_from_payload(payload)
    if normalized in {"macos", "macos.frontmost-app", "macos-frontmost-app"}:
        return MacOSAppContextSourcePort().context_from_payload(payload)
    specific = _specific_macos_port(normalized)
    if specific is not None:
        return specific.context_from_payload(payload)
    raise ActiveContextError(f"unknown active-context source kind: {kind!r}")


def source_port_for_active_context(kind: str) -> SourcePort:
    normalized = kind.lower().replace("_", "-")
    if normalized in {"browser", "browser.current-page", "browser-current-page"}:
        return BrowserCurrentPageSourcePort()
    if normalized in {"macos", "macos.frontmost-app", "macos-frontmost-app"}:
        return MacOSAppContextSourcePort()
    specific = _specific_macos_port(normalized)
    if specific is not None:
        return specific
    raise ActiveContextError(f"unknown active-context source kind: {kind!r}")


def _canonical_http_url(raw: str) -> str:
    parsed = urlparse(str(raw or "").strip())
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ActiveContextError(f"browser active context requires http(s) URL: {raw!r}")
    if not parsed.netloc:
        raise ActiveContextError(f"browser URL missing host: {raw!r}")

    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower() if parsed.hostname else ""
    if not host:
        raise ActiveContextError(f"browser URL missing host: {raw!r}")
    port = parsed.port
    netloc = host
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    if port is not None and not default_port:
        netloc = f"{host}:{port}"

    path = quote(parsed.path or "/", safe="/:@")
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query = urlencode(sorted(query_pairs), doseq=True)
    return urlunparse((scheme, netloc, path, "", query, ""))


def _canonical_file_uri(raw: str) -> str:
    parsed = urlparse(raw)
    if parsed.scheme.lower() != "file":
        raise ActiveContextError(f"not a file URI: {raw!r}")
    path = Path(parsed.path).expanduser()
    if not path.is_absolute():
        raise ActiveContextError(f"file URI must be absolute: {raw!r}")
    return path.resolve(strict=False).as_uri()


def _stable_scheme_payload(parsed: Any) -> str:
    netloc = parsed.netloc.lower()
    path = quote(parsed.path or "", safe="/:@")
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query = urlencode(sorted(query_pairs), doseq=True)
    payload = urlunparse(("", netloc, path, "", query, ""))
    payload = payload.lstrip("/")
    if not payload:
        raise ActiveContextError(f"active-context URI lacks stable payload: {parsed.geturl()!r}")
    return payload


_STABLE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.@<>=:+%/-]{1,512}$")


def _stable_token(value: str, *, kind: str) -> str:
    token = str(value or "").strip()
    if not token or not _STABLE_TOKEN_RE.match(token):
        raise ActiveContextError(f"cannot canonicalize {kind} id from {value!r}")
    return token


def _canonical_iwork_resource(app: str, uri: str) -> str:
    raw = str(uri or "").strip()
    if raw.startswith(f"{app}:document:"):
        token = raw.removeprefix(f"{app}:document:")
        return f"macos:{app}:document:{_stable_token(token, kind=f'{app} document')}"
    parsed = urlparse(raw)
    scheme = parsed.scheme.lower()
    if scheme == "file":
        return f"macos:{app}:file:{_canonical_file_uri(raw)}"
    if scheme == app:
        token = _stable_scheme_payload(parsed)
        return f"macos:{app}:document:{_stable_token(token, kind=f'{app} document')}"
    raise ActiveContextError(f"{app} context requires file://, {app}:, or {app}:document: URI")


def _canonical_office_resource(app: str, uri: str) -> str:
    raw = str(uri or "").strip()
    if raw.startswith(f"{app}:document:"):
        token = raw.removeprefix(f"{app}:document:")
        return f"macos:{app}:document:{_stable_token(token, kind=f'{app} document')}"
    if app == "outlook" and raw.startswith(f"{app}:message:"):
        token = raw.removeprefix(f"{app}:message:")
        return f"macos:{app}:message:{_stable_token(token, kind='Outlook message')}"
    parsed = urlparse(raw)
    scheme = parsed.scheme.lower()
    if scheme == "file":
        return f"macos:{app}:file:{_canonical_file_uri(raw)}"
    if scheme == app:
        token = _stable_scheme_payload(parsed)
        kind = "Outlook message" if app == "outlook" else f"{app} document"
        noun = "message" if app == "outlook" else "document"
        return f"macos:{app}:{noun}:{_stable_token(token, kind=kind)}"
    accepted = f"file://, {app}:, or {app}:document:"
    if app == "outlook":
        accepted = f"{app}:, {app}:message:, or {app}:document:"
    raise ActiveContextError(f"{app} context requires {accepted} URI")


def _specific_macos_port(kind: str) -> _MacOSSpecificAppSourcePort | None:
    if kind in {"apple-mail", "mail", "macos.apple-mail"}:
        return AppleMailContextSourcePort()
    if kind in {"finder", "macos.finder"}:
        return FinderContextSourcePort()
    if kind in {"pages", "macos.pages"}:
        return PagesContextSourcePort()
    if kind in {"numbers", "macos.numbers"}:
        return NumbersContextSourcePort()
    if kind in {"keynote", "macos.keynote"}:
        return KeynoteContextSourcePort()
    if kind in {"outlook", "microsoft-outlook", "macos.outlook"}:
        return OutlookContextSourcePort()
    if kind in {"word", "microsoft-word", "macos.word"}:
        return WordContextSourcePort()
    if kind in {"powerpoint", "microsoft-powerpoint", "macos.powerpoint"}:
        return PowerPointContextSourcePort()
    if kind in {"calendar", "apple-calendar", "macos.calendar"}:
        return CalendarContextSourcePort()
    return None


def _coerce_captured_at(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _ensure_aware(value)
    if isinstance(value, str) and value.strip():
        return _ensure_aware(datetime.fromisoformat(value))
    return _utcnow()


def _metadata_without(payload: dict[str, Any], excluded: set[str]) -> dict[str, Any]:
    return {str(k): v for k, v in payload.items() if str(k) not in excluded}
