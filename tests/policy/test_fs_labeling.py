"""Tests for dynamic filesystem labeling (#5)."""

from __future__ import annotations

import pytest

from capabledeputy.policy.fs_labeling import (
    FsLabelRuleError,
    load_fs_label_rules,
    parse_fs_label_rules,
)
from capabledeputy.policy.labels import ProvenanceLevel
from capabledeputy.policy.tiers import Tier


def _labeler(rules):
    return parse_fs_label_rules(rules)


def test_empty_labeler_is_noop() -> None:
    lab = parse_fs_label_rules(None)
    out = lab.labels_for("/anything")
    assert not out.a and not out.b


def test_path_prefix_tier() -> None:
    lab = _labeler(
        [{"match": {"path_prefix": "/home/u/Financial/"}, "labels": ["confidential.financial"]}],
    )
    out = lab.labels_for("/home/u/Financial/budget.pdf")
    assert {t.category for t in out.a} == {"financial"}
    # tier resolved from the catalog (financial = restricted, #50)
    assert {t.tier for t in out.a} == {Tier.RESTRICTED}
    # a non-matching path gets nothing
    assert not lab.labels_for("/etc/hosts").a


def test_filename_glob_tier() -> None:
    lab = _labeler(
        [
            {
                "match": {"filename_glob": ["*.key", "id_rsa*"]},
                "labels": ["confidential.credentials"],
            },
        ],
    )
    assert {t.category for t in lab.labels_for("/x/a.key").a} == {"credentials"}
    assert {t.category for t in lab.labels_for("/x/id_rsa").a} == {"credentials"}
    assert not lab.labels_for("/x/notes.txt").a


def test_content_regex_only_applies_with_content() -> None:
    lab = _labeler(
        [
            {
                "match": {"content_regex": "BEGIN OPENSSH PRIVATE KEY"},
                "labels": ["confidential.credentials"],
            },
        ],
    )
    assert lab.any_content_rules
    # Without content, the content rule cannot match.
    assert not lab.labels_for("/x/anything").a
    # With content, it raises.
    out = lab.labels_for("/x/anything", content="-----BEGIN OPENSSH PRIVATE KEY-----")
    assert {t.category for t in out.a} == {"credentials"}


def test_provenance_label() -> None:
    lab = _labeler([{"match": {"path_prefix": "/downloads/"}, "labels": ["untrusted.external"]}])
    out = lab.labels_for("/downloads/x.bin")
    assert {t.level for t in out.b} == {ProvenanceLevel.EXTERNAL_UNTRUSTED}


def test_multiple_rules_compose_raise_only() -> None:
    lab = _labeler(
        [
            {"match": {"path_prefix": "/d/"}, "labels": ["confidential.financial"]},
            {"match": {"filename_glob": "*.key"}, "labels": ["confidential.credentials"]},
        ],
    )
    out = lab.labels_for("/d/secret.key")
    assert {t.category for t in out.a} == {"financial", "credentials"}


def test_malformed_rules_fail_closed() -> None:
    with pytest.raises(FsLabelRuleError):
        parse_fs_label_rules([{"labels": ["confidential.financial"]}])  # no match
    with pytest.raises(FsLabelRuleError):
        parse_fs_label_rules([{"match": {"path_prefix": "/x"}}])  # no labels
    with pytest.raises(FsLabelRuleError):
        parse_fs_label_rules([{"match": {}, "labels": ["confidential.financial"]}])  # empty match
    with pytest.raises(FsLabelRuleError):
        parse_fs_label_rules([{"match": {"path_prefix": "/x"}, "labels": ["bogus.label"]}])


def test_load_absent_file_is_noop(tmp_path) -> None:
    lab = load_fs_label_rules(tmp_path / "nope.yaml")
    assert not lab.rules


def test_shipped_example_parses() -> None:
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    lab = load_fs_label_rules(repo / "configs" / "fs_label_rules.example.yaml")
    assert lab.rules
    # The example labels SSH keys as credentials.
    out = lab.labels_for("/home/u/.ssh/id_rsa")
    assert {t.category for t in out.a} == {"credentials"}


async def test_fs_read_handler_attaches_labels(tmp_path) -> None:
    """End-to-end: make_fs_tools(labeler) wires the labeler so fs.read
    returns the matched Axis-A labels on additional_tags."""
    from uuid import uuid4

    from capabledeputy.policy.labels import LabelState
    from capabledeputy.tools.native.fs import make_fs_tools
    from capabledeputy.tools.registry import ToolContext

    secret = tmp_path / "secret.key"
    secret.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n")
    lab = _labeler(
        [{"match": {"filename_glob": "*.key"}, "labels": ["confidential.credentials"]}],
    )
    tools = {t.name: t for t in make_fs_tools(lab)}
    context = ToolContext(session_id=uuid4(), label_state=LabelState())
    result = await tools["fs.read"].handler({"path": str(secret)}, context)
    assert result.output["ok"] is True
    assert {t.category for t in result.additional_tags.a} == {"credentials"}
    # Provenance from the base handler is preserved (raise-only compose).
    assert {t.level for t in result.additional_tags.b} == {ProvenanceLevel.EXTERNAL_UNTRUSTED}


async def test_fs_read_handler_no_labeler_unchanged(tmp_path) -> None:
    from uuid import uuid4

    from capabledeputy.policy.labels import LabelState
    from capabledeputy.tools.native.fs import make_fs_tools
    from capabledeputy.tools.registry import ToolContext

    f = tmp_path / "f.txt"
    f.write_text("hello")
    tools = {t.name: t for t in make_fs_tools(None)}
    context = ToolContext(session_id=uuid4(), label_state=LabelState())
    result = await tools["fs.read"].handler({"path": str(f)}, context)
    assert not result.additional_tags.a  # no category labels without a labeler
