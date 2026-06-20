"""Tests for the upstream MCP adapter's resources surface.

Verifies that upstream servers' resources/list and resources/read
are exposed in the same shape as the native StaticResourcePublisher
+ ResourcesTool, with inherent labels propagating from the upstream
config.
"""

from __future__ import annotations

import pytest

from capabledeputy.policy.labels import LabelState, ProvenanceLevel, ProvenanceTag
from capabledeputy.upstream.adapter import LabeledMcpAdapter
from capabledeputy.upstream.config import UpstreamServerConfig


class _FakeResource:
    def __init__(self, uri, name="", description="", mime="text/plain", meta=None):
        self.uri = uri
        self.name = name
        self.description = description
        self.mimeType = mime
        self.meta = meta or {}


class _FakeListResourcesResult:
    def __init__(self, resources):
        self.resources = resources


class _FakeContent:
    def __init__(self, text, mime="text/plain", meta=None):
        self.text = text
        self.mimeType = mime
        self.meta = meta or {}


class _FakeReadResourceResult:
    def __init__(self, contents):
        self.contents = contents


class _FakeSession:
    def __init__(self, resources=None, content_for=None, raise_on=None):
        self._resources = resources or []
        self._content_for = content_for or {}
        self._raise_on = raise_on or set()

    async def list_resources(self):
        if "list_resources" in self._raise_on:
            raise RuntimeError("upstream does not support resources")
        return _FakeListResourcesResult(self._resources)

    async def read_resource(self, uri):
        # The real ClientSession.read_resource takes an AnyUrl; the adapter
        # passes one. Normalize to str so the fixture's str-keyed maps match.
        uri = str(uri)
        if uri in self._raise_on:
            raise RuntimeError(f"resource missing: {uri}")
        contents = self._content_for.get(uri, [])
        return _FakeReadResourceResult(contents)


def _make_adapter(session, *, inherent_tags=None):
    if inherent_tags is None:
        inherent_tags = LabelState()
    config = UpstreamServerConfig(
        name="bundled-test",
        command=("echo",),
        inherent_tags=inherent_tags,
    )
    return LabeledMcpAdapter(config=config, session=session)


@pytest.mark.asyncio
async def test_list_upstream_resources_basic() -> None:
    session = _FakeSession(
        resources=[
            _FakeResource(
                uri="upstream://doc/cv.md",
                name="CV",
                description="Resume",
                mime="text/markdown",
            ),
            _FakeResource(
                uri="upstream://doc/notes.md",
                name="Notes",
            ),
        ],
    )
    adapter = _make_adapter(session)
    catalog = await adapter.list_upstream_resources()
    assert len(catalog) == 2
    uris = {r["uri"] for r in catalog}
    assert "upstream://doc/cv.md" in uris
    assert "upstream://doc/notes.md" in uris
    # Server name attached for trace
    for entry in catalog:
        assert entry["server"] == "bundled-test"


@pytest.mark.asyncio
async def test_list_upstream_resources_propagates_inherent_labels() -> None:
    session = _FakeSession(
        resources=[_FakeResource(uri="upstream://doc/x.md", name="X")],
    )
    adapter = _make_adapter(
        session,
        inherent_tags=LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)})),
    )
    catalog = await adapter.list_upstream_resources()
    assert "external-untrusted" in catalog[0]["labels"]


@pytest.mark.asyncio
async def test_list_upstream_resources_returns_empty_when_unsupported() -> None:
    """Servers without resources/list shouldn't crash the adapter."""
    session = _FakeSession(raise_on={"list_resources"})
    adapter = _make_adapter(session)
    catalog = await adapter.list_upstream_resources()
    assert catalog == []


@pytest.mark.asyncio
async def test_list_upstream_resources_skips_missing_uri() -> None:
    """Resources without a uri are filtered out (defensive)."""
    session = _FakeSession(
        resources=[
            _FakeResource(uri="upstream://doc/ok.md", name="OK"),
            _FakeResource(uri="", name="bad"),
        ],
    )
    adapter = _make_adapter(session)
    catalog = await adapter.list_upstream_resources()
    assert len(catalog) == 1
    assert catalog[0]["uri"] == "upstream://doc/ok.md"


@pytest.mark.asyncio
async def test_read_upstream_resource_returns_content() -> None:
    session = _FakeSession(
        content_for={
            "upstream://doc/cv.md": [_FakeContent("# Resume\n\nAlice.", mime="text/markdown")],
        },
    )
    adapter = _make_adapter(session)
    result = await adapter.read_upstream_resource("upstream://doc/cv.md")
    assert result["found"] is True
    assert "Alice" in result["content"]
    assert result["mime_type"] == "text/markdown"
    assert result["server"] == "bundled-test"


@pytest.mark.asyncio
async def test_read_upstream_resource_propagates_inherent_labels() -> None:
    session = _FakeSession(
        content_for={"upstream://x.md": [_FakeContent("content")]},
    )
    adapter = _make_adapter(
        session,
        inherent_tags=LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)})),
    )
    result = await adapter.read_upstream_resource("upstream://x.md")
    assert "external-untrusted" in result["labels"]


@pytest.mark.asyncio
async def test_read_upstream_resource_propagates_content_meta_labels() -> None:
    session = _FakeSession(
        content_for={
            "upstream://x.md": [
                _FakeContent(
                    "content",
                    meta={
                        "io.capabledeputy/inherent_tags": {
                            "a": [
                                {
                                    "kind": "category",
                                    "category": "financial",
                                    "tier": "restricted",
                                    "assignment_provenance": "source-declared",
                                },
                            ],
                        },
                    },
                ),
            ],
        },
    )
    adapter = _make_adapter(session)
    result = await adapter.read_upstream_resource("upstream://x.md")
    assert "financial" in result["labels"]


@pytest.mark.asyncio
async def test_read_upstream_resource_handles_failure() -> None:
    session = _FakeSession(raise_on={"upstream://broken.md"})
    adapter = _make_adapter(session)
    result = await adapter.read_upstream_resource("upstream://broken.md")
    assert result["found"] is False
    assert "resource missing" in result.get("error", "")
