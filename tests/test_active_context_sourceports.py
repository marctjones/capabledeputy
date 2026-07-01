from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from capabledeputy.daemon.source_context_handlers import make_source_context_handlers
from capabledeputy.substrate.active_context import (
    ActiveContextError,
    ActiveContextRecord,
    BrowserCurrentPageSourcePort,
    MacOSAppContextSourcePort,
    active_context_from_payload,
)
from capabledeputy.substrate.source_port import get_source_port


def test_browser_context_canonicalizes_url_and_labels_untrusted() -> None:
    port = BrowserCurrentPageSourcePort()

    record = port.context_from_payload(
        {
            "url": "HTTPS://Example.COM:443/a path?z=2&a=1#section",
            "title": "Example",
            "stale_after_seconds": 30,
        },
    )

    assert record.canonical_id == "browser:url:https://example.com/a%20path?a=1&z=2"
    assert record.source_kind == "browser.current-page"
    assert record.title == "Example"
    assert any(tag.level.value == "external-untrusted" for tag in record.labels.b)


def test_browser_context_rejects_non_http_url() -> None:
    with pytest.raises(ActiveContextError):
        BrowserCurrentPageSourcePort().canonicalize_resource("file:///tmp/secret.txt")


def test_macos_context_canonicalizes_file_uri(tmp_path: Path) -> None:
    doc = tmp_path / "Draft.pages"
    uri = doc.as_uri()

    record = active_context_from_payload(
        "macos.frontmost-app",
        {
            "uri": uri,
            "app_bundle_id": "com.apple.Pages",
            "title": "Draft",
            "stale_after_seconds": 60,
        },
    )

    assert record.canonical_id == f"macos:file:{uri}"
    assert record.metadata["app_bundle_id"] == "com.apple.Pages"
    assert any(tag.level.value == "system-internal" for tag in record.labels.b)


def test_macos_context_rejects_ambiguous_plain_path() -> None:
    with pytest.raises(ActiveContextError):
        MacOSAppContextSourcePort().canonicalize_resource("Documents/Draft.pages")


def test_active_context_stale_records_fail_closed() -> None:
    captured = datetime.now(UTC) - timedelta(minutes=10)
    record = ActiveContextRecord(
        source_kind="browser.current-page",
        uri="https://example.com",
        canonical_id="browser:url:https://example.com/",
        captured_at=captured,
        stale_after_seconds=60,
    )

    assert record.is_stale()
    with pytest.raises(ActiveContextError):
        record.ensure_fresh()


def test_active_context_source_ports_are_registry_accessible() -> None:
    assert isinstance(get_source_port("browser.current-page"), BrowserCurrentPageSourcePort)
    assert isinstance(get_source_port("macos.frontmost-app"), MacOSAppContextSourcePort)


async def test_source_context_handlers_import_and_canonicalize() -> None:
    handlers = make_source_context_handlers()

    imported = await handlers["source_context.import"](
        {"kind": "browser", "url": "https://example.com/path#frag"},
    )
    canonical = await handlers["source_context.canonicalize"](
        {"kind": "browser", "url": "https://example.com/path#frag"},
    )

    assert imported["canonical_id"] == "browser:url:https://example.com/path"
    assert canonical["canonical_id"] == imported["canonical_id"]
