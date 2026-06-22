"""Tests for incoming-email labeling (#34)."""

from __future__ import annotations

import pytest

from capabledeputy.policy.email_labeling import (
    EmailLabelRuleError,
    extract_email_fields,
    load_email_label_rules,
    parse_email_label_rules,
)
from capabledeputy.policy.labels import LabelState, ProvenanceLevel, ProvenanceTag
from capabledeputy.policy.tiers import Tier


def _lab(rules):
    return parse_email_label_rules(rules)


def test_empty_labeler_is_noop() -> None:
    lab = parse_email_label_rules(None)
    assert not lab.labels_for({"from": "x@y.com", "subject": "hi", "body": ""}).a


def test_from_domain_match() -> None:
    lab = _lab([{"match": {"from_domain": "chase.com"}, "labels": ["confidential.financial"]}])
    out = lab.labels_for({"from": "alerts@chase.com", "subject": "", "body": ""})
    assert {t.category for t in out.a} == {"financial"}
    # tier from catalog (financial = restricted, #50)
    assert {t.tier for t in out.a} == {Tier.RESTRICTED}
    # display-name form also matches
    out2 = lab.labels_for({"from": "Chase <alerts@chase.com>", "subject": "", "body": ""})
    assert {t.category for t in out2.a} == {"financial"}
    # non-match
    assert not lab.labels_for({"from": "a@notchase.com", "subject": "", "body": ""}).a


def test_subject_regex_case_insensitive() -> None:
    lab = _lab([{"match": {"subject_regex": "invoice"}, "labels": ["confidential.financial"]}])
    out = lab.labels_for({"from": "x@y.com", "subject": "Your INVOICE is ready", "body": ""})
    assert {t.category for t in out.a} == {"financial"}


def test_body_regex_only_with_body() -> None:
    lab = _lab([{"match": {"body_regex": "diagnosis"}, "labels": ["confidential.health"]}])
    assert lab.any_body_rules
    assert not lab.labels_for({"from": "x@y.com", "subject": "s", "body": ""}).a
    out = lab.labels_for({"from": "x@y.com", "subject": "s", "body": "your diagnosis is..."})
    assert {t.category for t in out.a} == {"health"}


def test_malformed_rules_fail_closed() -> None:
    with pytest.raises(EmailLabelRuleError):
        parse_email_label_rules([{"labels": ["confidential.financial"]}])  # no match
    with pytest.raises(EmailLabelRuleError):
        parse_email_label_rules([{"match": {"from_domain": "x"}}])  # no labels
    with pytest.raises(EmailLabelRuleError):
        parse_email_label_rules([{"match": {}, "labels": ["confidential.financial"]}])
    with pytest.raises(EmailLabelRuleError):
        parse_email_label_rules([{"match": {"from_domain": "x"}, "labels": ["bogus"]}])


def test_extract_email_fields_tolerant() -> None:
    f = extract_email_fields({"sender": "A@B.com", "Subject": "Hi", "snippet": "yo"})
    assert f["from"] == "a@b.com"
    assert f["subject"] == "Hi"
    assert f["body"] == "yo"
    # non-dict ⇒ empty fields
    assert extract_email_fields("nope")["from"] == ""


def test_labels_for_output_convenience() -> None:
    lab = _lab([{"match": {"from_domain": "chase.com"}, "labels": ["confidential.financial"]}])
    out = lab.labels_for_output({"from": "x@chase.com", "subject": "s", "body": ""})
    assert {t.category for t in out.a} == {"financial"}


def test_labels_for_message_preserves_base_floor_and_adds_specific_labels() -> None:
    lab = _lab([{"match": {"from_domain": "chase.com"}, "labels": ["confidential.financial"]}])
    base = LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)}))

    out = lab.labels_for_message(
        {"from": "alerts@chase.com", "subject": "statement", "body": ""},
        base=base,
    )

    assert {t.category for t in out.a} == {"financial"}
    assert {t.level for t in out.b} == {ProvenanceLevel.EXTERNAL_UNTRUSTED}


def test_shipped_example_parses() -> None:
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    lab = load_email_label_rules(repo / "configs" / "email_label_rules.example.yaml")
    assert lab.rules
    out = lab.labels_for_output({"from": "alerts@chase.com", "subject": "", "body": ""})
    assert {t.category for t in out.a} == {"financial"}


async def test_adapter_result_labeler_hook() -> None:
    """The generic result_labeler hook on the upstream adapter merges
    per-message labels (raise-only) into the read result's additional_tags."""
    from types import SimpleNamespace
    from uuid import UUID

    from capabledeputy.policy.labels import LabelState
    from capabledeputy.tools.registry import ToolContext
    from capabledeputy.upstream.adapter import LabeledMcpAdapter
    from capabledeputy.upstream.config import UpstreamServerConfig

    lab = _lab([{"match": {"from_domain": "chase.com"}, "labels": ["confidential.financial"]}])

    msg = {"from": "alerts@chase.com", "subject": "statement"}
    # Mimic the MCP SDK result shape (camelCase attrs come from the SDK).
    upstream_result = SimpleNamespace(isError=False, content=[], structuredContent=msg)

    class _Session:
        async def call_tool(self, name, arguments=None):
            return upstream_result

    cfg = UpstreamServerConfig(name="gmail", command=("x",))
    adapter = LabeledMcpAdapter(
        cfg,
        _Session(),  # type: ignore[arg-type]
        result_labeler=lambda _n, _a, output: lab.labels_for_output(output),
    )
    handler = adapter._make_handler("get_gmail_message")
    context = ToolContext(
        session_id=UUID("00000000-0000-0000-0000-000000000000"),
        label_state=LabelState(),
    )
    r1 = await handler({"id": "1"}, context)
    assert {t.category for t in r1.additional_tags.a} == {"financial"}
    # A labeler error must not break the read (best-effort enrichment).
    adapter2 = LabeledMcpAdapter(
        cfg,
        _Session(),  # type: ignore[arg-type]
        result_labeler=lambda _n, _a, _o: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    h2 = adapter2._make_handler("get_gmail_message")
    r2 = await h2({"id": "1"}, context)
    assert r2.output  # still returns the message
    assert not r2.additional_tags.a  # no labels, but no crash
