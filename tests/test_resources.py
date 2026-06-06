"""Tests for the operator-resources surface.

Covers:
  - YAML parser for configs/resources.yaml
  - StaticResourcePublisher list / get semantics
  - resources.list + resources.read tool handlers
  - Inherent labels propagate via ToolResult.additional_labels
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from capabledeputy.policy.labels import CategoryTag, LabelState
from capabledeputy.policy.tiers import Tier
from capabledeputy.resources.static import (
    Resource,
    ResourceError,
    StaticResourcePublisher,
    load_static_resources,
)
from capabledeputy.tools.native.resources import make_resources_tools
from capabledeputy.tools.registry import ToolContext

# ---------- parser ----------


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    publisher = load_static_resources(tmp_path / "nonexistent.yaml")
    assert publisher.resources == ()


def test_load_empty_yaml_returns_empty(tmp_path: Path) -> None:
    cfg = tmp_path / "resources.yaml"
    cfg.write_text("", encoding="utf-8")
    publisher = load_static_resources(cfg)
    assert publisher.resources == ()


def test_load_basic_resource(tmp_path: Path) -> None:
    content_file = tmp_path / "cv.md"
    content_file.write_text("# My CV\n")
    cfg = tmp_path / "resources.yaml"
    cfg.write_text(
        f"""
resources:
  - uri: "doc://cv.md"
    name: "Current CV"
    description: "Resume for job apps"
    mime_type: "text/markdown"
    content_path: "{content_file}"
    labels: ["confidential.personal"]
""",
        encoding="utf-8",
    )
    publisher = load_static_resources(cfg)
    assert len(publisher.resources) == 1
    r = publisher.resources[0]
    assert r.uri == "doc://cv.md"
    assert r.name == "Current CV"
    assert r.mime_type == "text/markdown"
    assert any(c.category == "personal" for c in r.tags.a)


def test_load_relative_path_refused(tmp_path: Path) -> None:
    cfg = tmp_path / "resources.yaml"
    cfg.write_text(
        """
resources:
  - uri: "doc://x.md"
    name: "X"
    content_path: "relative/path.md"
""",
        encoding="utf-8",
    )
    with pytest.raises(ResourceError, match="must be absolute"):
        load_static_resources(cfg)


def test_load_duplicate_uri_refused(tmp_path: Path) -> None:
    cfg = tmp_path / "resources.yaml"
    cfg.write_text(
        f"""
resources:
  - uri: "doc://x.md"
    name: "X"
    content_path: "{tmp_path / "a.md"}"
  - uri: "doc://x.md"
    name: "X2"
    content_path: "{tmp_path / "b.md"}"
""",
        encoding="utf-8",
    )
    with pytest.raises(ResourceError, match="duplicate uri"):
        load_static_resources(cfg)


def test_load_unknown_label_refused(tmp_path: Path) -> None:
    cfg = tmp_path / "resources.yaml"
    cfg.write_text(
        f"""
resources:
  - uri: "doc://x.md"
    name: "X"
    content_path: "{tmp_path / "x.md"}"
    labels: ["not.a.real.label"]
""",
        encoding="utf-8",
    )
    with pytest.raises(ResourceError, match="unknown label"):
        load_static_resources(cfg)


def test_load_missing_required_field_refused(tmp_path: Path) -> None:
    cfg = tmp_path / "resources.yaml"
    cfg.write_text(
        """
resources:
  - uri: "doc://x.md"
    name: "X"
    # missing content_path
""",
        encoding="utf-8",
    )
    with pytest.raises(ResourceError, match="missing 'content_path'"):
        load_static_resources(cfg)


# ---------- publisher API ----------


def test_publisher_list_with_prefix(tmp_path: Path) -> None:
    pub = StaticResourcePublisher(
        resources=(
            Resource(
                uri="doc://cv.md",
                name="CV",
                description="",
                mime_type="text/markdown",
                content_path=tmp_path / "cv.md",
            ),
            Resource(
                uri="doc://style-guide.md",
                name="Style guide",
                description="",
                mime_type="text/markdown",
                content_path=tmp_path / "sg.md",
            ),
            Resource(
                uri="brief://project-alpha",
                name="Project Alpha brief",
                description="",
                mime_type="text/plain",
                content_path=tmp_path / "alpha.txt",
            ),
        ),
    )
    all_resources = pub.list()
    assert len(all_resources) == 3
    docs = pub.list("doc://")
    assert len(docs) == 2
    briefs = pub.list("brief://")
    assert len(briefs) == 1


def test_publisher_get_returns_none_for_unknown(tmp_path: Path) -> None:
    pub = StaticResourcePublisher(resources=())
    assert pub.get("doc://unknown") is None


# ---------- tool handlers ----------


@pytest.mark.asyncio
async def test_resources_list_tool(tmp_path: Path) -> None:
    pub = StaticResourcePublisher(
        resources=(
            Resource(
                uri="doc://cv.md",
                name="CV",
                description="Resume",
                mime_type="text/markdown",
                content_path=tmp_path / "cv.md",
                tags=LabelState(
                    a=frozenset(
                        {
                            CategoryTag(
                                "personal",
                                Tier.REGULATED,
                                assignment_provenance="source-declared",
                            ),
                        }
                    )
                ),
            ),
        ),
    )
    tools = make_resources_tools(pub)
    list_tool = next(t for t in tools if t.name == "resources.list")
    ctx = ToolContext(session_id=uuid4(), label_state=LabelState())
    result = await list_tool.handler({}, ctx)
    assert result.output["count"] == 1
    entry = result.output["resources"][0]
    assert entry["uri"] == "doc://cv.md"
    assert entry["name"] == "CV"
    # tags is now a dict with structure {"a": [...], "b": [...]}
    tags_dict = entry.get("tags", {})
    assert tags_dict is not None


@pytest.mark.asyncio
async def test_resources_read_tool(tmp_path: Path) -> None:
    content = tmp_path / "cv.md"
    content.write_text("# Alice\n\nSoftware engineer.\n")
    pub = StaticResourcePublisher(
        resources=(
            Resource(
                uri="doc://cv.md",
                name="CV",
                description="",
                mime_type="text/markdown",
                content_path=content,
                tags=LabelState(
                    a=frozenset(
                        {
                            CategoryTag(
                                "personal",
                                Tier.REGULATED,
                                assignment_provenance="source-declared",
                            ),
                        }
                    )
                ),
            ),
        ),
    )
    tools = make_resources_tools(pub)
    read_tool = next(t for t in tools if t.name == "resources.read")
    ctx = ToolContext(session_id=uuid4(), label_state=LabelState())
    result = await read_tool.handler({"uri": "doc://cv.md"}, ctx)
    assert result.output["found"] is True
    assert "Software engineer." in result.output["content"]
    # Inherent tag flows back so the chokepoint can propagate it
    # into the session's label_state.
    assert any(c.category == "personal" for c in result.additional_tags.a)


@pytest.mark.asyncio
async def test_resources_read_unknown_uri(tmp_path: Path) -> None:
    pub = StaticResourcePublisher(resources=())
    tools = make_resources_tools(pub)
    read_tool = next(t for t in tools if t.name == "resources.read")
    ctx = ToolContext(session_id=uuid4(), label_state=LabelState())
    result = await read_tool.handler({"uri": "doc://nope"}, ctx)
    assert result.output["found"] is False


@pytest.mark.asyncio
async def test_resources_read_missing_content_file(tmp_path: Path) -> None:
    """Resource declared but the content file vanished — tool returns
    found=false with an error message, not a crash."""
    pub = StaticResourcePublisher(
        resources=(
            Resource(
                uri="doc://gone.md",
                name="Gone",
                description="",
                mime_type="text/markdown",
                content_path=tmp_path / "does-not-exist.md",
            ),
        ),
    )
    tools = make_resources_tools(pub)
    read_tool = next(t for t in tools if t.name == "resources.read")
    ctx = ToolContext(session_id=uuid4(), label_state=LabelState())
    result = await read_tool.handler({"uri": "doc://gone.md"}, ctx)
    assert result.output["found"] is False
    assert "missing" in result.output.get("error", "")


@pytest.mark.asyncio
async def test_resources_read_too_large(tmp_path: Path) -> None:
    big = tmp_path / "big.txt"
    # Larger than the 256K cap
    big.write_text("x" * (260 * 1024))
    pub = StaticResourcePublisher(
        resources=(
            Resource(
                uri="doc://big.txt",
                name="big",
                description="",
                mime_type="text/plain",
                content_path=big,
            ),
        ),
    )
    tools = make_resources_tools(pub)
    read_tool = next(t for t in tools if t.name == "resources.read")
    ctx = ToolContext(session_id=uuid4(), label_state=LabelState())
    result = await read_tool.handler({"uri": "doc://big.txt"}, ctx)
    assert result.output["found"] is False
    assert "too large" in result.output.get("error", "")
