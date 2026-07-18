"""#326 — native web.fetch is a REAL, bounded, SSRF-guarded HTTP(S) fetch. The
destination-gated egress floor runs upstream at the policy chokepoint; this
covers the actuator: scheme + SSRF guards, the offline WebMock override, and the
error path. No test hits the real network — the SSRF guard rejects internal
targets before any socket connect, and the success/error branches are
monkeypatched."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

import capabledeputy.tools.native.web as webmod
from capabledeputy.policy.labels import LabelState, ProvenanceLevel, ProvenanceTag
from capabledeputy.tools.native.web import (
    WebFetchError,
    WebMock,
    _fetch_url_text,
    _ip_is_internal,
    make_web_tools,
)
from capabledeputy.tools.registry import ToolContext


def _ctx() -> ToolContext:
    return ToolContext(session_id=uuid4(), label_state=LabelState())


def _fetch_handler(mock: WebMock):
    return next(t for t in make_web_tools(mock) if t.name == "web.fetch").handler


# --- SSRF + scheme guards (no network) ---------------------------------------


@pytest.mark.parametrize(
    "ip",
    ["127.0.0.1", "::1", "10.0.0.1", "192.168.1.1", "169.254.169.254", "0.0.0.0"],
)
def test_internal_ips_are_blocked(ip: str) -> None:
    assert _ip_is_internal(ip) is True


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1"])
def test_public_ips_pass(ip: str) -> None:
    assert _ip_is_internal(ip) is False


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost/admin",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://10.0.0.5/",
    ],
)
def test_fetch_refuses_internal_targets(url: str) -> None:
    # getaddrinfo resolves to an internal IP -> refused before any connect.
    with pytest.raises(WebFetchError, match=r"internal/private|could not resolve"):
        _fetch_url_text(url)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "gopher://x/"])
def test_fetch_refuses_non_http_schemes(url: str) -> None:
    with pytest.raises(WebFetchError, match="scheme"):
        _fetch_url_text(url)


def test_fetch_refuses_url_without_host() -> None:
    with pytest.raises(WebFetchError, match="no host"):
        _fetch_url_text("http:///nohost")


# --- handler behavior --------------------------------------------------------


async def test_mock_override_wins_offline() -> None:
    mock = WebMock()
    mock.serve("https://example.com/", "<preloaded/>")
    result = await _fetch_handler(mock)({"url": "https://example.com/"}, _ctx())
    assert result.output["found"] is True
    assert result.output["body"] == "<preloaded/>"
    assert result.output["source"] == "mock"
    # Always labeled untrusted.external — the whole point of the tool.
    assert ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED) in result.additional_tags.b


async def test_network_success_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake(url: str) -> dict[str, Any]:
        return {"body": "hello", "status": 200, "content_type": "text/html", "truncated": False}

    monkeypatch.setattr(webmod, "_fetch_url_text", _fake)
    result = await _fetch_handler(WebMock())({"url": "https://example.com/"}, _ctx())
    assert result.output["found"] is True
    assert result.output["source"] == "network"
    assert result.output["body"] == "hello"
    assert result.output["status"] == 200
    assert ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED) in result.additional_tags.b


async def test_network_error_branch_is_surfaced_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(url: str) -> dict[str, Any]:
        raise WebFetchError("refusing to fetch internal/private address 127.0.0.1")

    monkeypatch.setattr(webmod, "_fetch_url_text", _boom)
    result = await _fetch_handler(WebMock())({"url": "http://127.0.0.1/"}, _ctx())
    assert result.output["found"] is False
    assert "internal/private" in result.output["error"]
    # Still labeled untrusted even on failure (fail-closed labeling).
    assert ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED) in result.additional_tags.b


def test_size_and_timeout_bounds_are_set() -> None:
    # Guardrails exist so a real fetch can't read unboundedly or hang forever.
    assert webmod._MAX_FETCH_BYTES <= 10 * 1024 * 1024
    assert webmod._FETCH_TIMEOUT_SECONDS <= 60
    assert webmod._ALLOWED_SCHEMES == ("http", "https")


# --- real _fetch_url_text body (urlopen + getaddrinfo mocked, no network) -----

_PUBLIC_ADDRINFO = [(2, 1, 6, "", ("93.184.216.34", 0))]  # example.com, public


class _FakeHeaders:
    def __init__(self, charset: str | None, ctype: str) -> None:
        self._charset = charset
        self._ctype = ctype

    def get_content_charset(self) -> str | None:
        return self._charset

    def get_content_type(self) -> str:
        return self._ctype


class _FakeResp:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        charset: str | None = "utf-8",
        ctype: str = "text/html",
    ) -> None:
        self._body = body
        self.status = status
        self.headers = _FakeHeaders(charset, ctype)

    def read(self, n: int) -> bytes:
        return self._body[:n]

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *_a: object) -> bool:
        return False


def _patch_public(monkeypatch: pytest.MonkeyPatch, resp_or_exc: Any) -> None:
    monkeypatch.setattr(webmod.socket, "getaddrinfo", lambda *a, **k: _PUBLIC_ADDRINFO)

    def _urlopen(req: Any, timeout: float = 0) -> Any:
        if isinstance(resp_or_exc, Exception):
            raise resp_or_exc
        return resp_or_exc

    monkeypatch.setattr(webmod.urllib.request, "urlopen", _urlopen)


def test_fetch_success_body_is_decoded(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_public(monkeypatch, _FakeResp(b"hello world"))
    out = webmod._fetch_url_text("https://example.com/")
    assert out["body"] == "hello world"
    assert out["status"] == 200
    assert out["content_type"] == "text/html"
    assert out["truncated"] is False


def test_fetch_truncates_oversized_body(monkeypatch: pytest.MonkeyPatch) -> None:
    big = b"x" * (webmod._MAX_FETCH_BYTES + 100)
    _patch_public(monkeypatch, _FakeResp(big, charset=None))  # charset None -> utf-8 fallback
    out = webmod._fetch_url_text("https://example.com/big")
    assert out["truncated"] is True
    assert len(out["body"]) == webmod._MAX_FETCH_BYTES


def test_fetch_wraps_urlerror(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    _patch_public(monkeypatch, urllib.error.URLError("dns boom"))
    with pytest.raises(WebFetchError, match="fetch failed"):
        webmod._fetch_url_text("https://example.com/")


def test_guard_wraps_resolution_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: object, **_k: object) -> Any:
        raise OSError("name resolution failed")

    monkeypatch.setattr(webmod.socket, "getaddrinfo", _boom)
    with pytest.raises(WebFetchError, match="could not resolve"):
        webmod._fetch_url_text("https://nonexistent.invalid/")
