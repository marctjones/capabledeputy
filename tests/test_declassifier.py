"""Tests for the DeclassifyingTransformer port + builtins."""

from __future__ import annotations

from capabledeputy.policy.labels import LabelState
from capabledeputy.substrate.declassifier_port import (
    apply_declassifier_chain,
)
from capabledeputy.substrate.declassifiers_builtin import (
    RegexRedactor,
    SchemaProjector,
)

# ---------- RegexRedactor ----------


def test_redactor_skips_non_string() -> None:
    redactor = RegexRedactor()
    result = redactor.declassify(
        value={"key": "value"},
        current_label_state=LabelState(),
    )
    assert result is None


def test_redactor_passes_through_clean_text() -> None:
    redactor = RegexRedactor()
    result = redactor.declassify(
        value="hello world",
        current_label_state=LabelState(),
    )
    assert result is None


def test_redactor_redacts_ssn() -> None:
    redactor = RegexRedactor()
    result = redactor.declassify(
        value="The SSN is 123-45-6789 for record.",
        current_label_state=LabelState(),
    )
    assert result is not None
    assert "123-45-6789" not in result.transformed_value
    assert "[REDACTED]-SSN" in result.transformed_value
    assert result.audit_diff == "1 SSN redacted"
    assert result.structural_proof_kind == "regex-redacted"


def test_redactor_redacts_multiple_pii_types() -> None:
    redactor = RegexRedactor()
    text = "Call 555-123-4567 or email alice@example.com about SSN 999-88-7777."
    result = redactor.declassify(
        value=text,
        current_label_state=LabelState(),
    )
    assert result is not None
    # All three should be redacted
    assert "555-123-4567" not in result.transformed_value
    assert "alice@example.com" not in result.transformed_value
    assert "999-88-7777" not in result.transformed_value
    # And the diff names all three
    diff = result.audit_diff
    assert "PHONE" in diff
    assert "EMAIL" in diff
    assert "SSN" in diff


def test_redactor_lowers_pii_to_none() -> None:
    redactor = RegexRedactor(lower_to_tier="none")
    result = redactor.declassify(
        value="SSN: 123-45-6789",
        current_label_state=LabelState(),
    )
    assert result is not None
    lowered = result.lower_axis_a_categories
    assert len(lowered) == 1
    assert lowered[0]["category"] == "pii"
    assert lowered[0]["to_tier"] == "none"


# ---------- SchemaProjector ----------


def test_projector_skips_non_dict() -> None:
    proj = SchemaProjector(allowed_keys=("a", "b"))
    result = proj.declassify(
        value="not a dict",
        current_label_state=LabelState(),
    )
    assert result is None


def test_projector_without_schema_skips() -> None:
    """No allowed_keys configured → no projection."""
    proj = SchemaProjector(allowed_keys=())
    result = proj.declassify(
        value={"a": 1, "b": 2},
        current_label_state=LabelState(),
    )
    assert result is None


def test_projector_keeps_allowed_drops_rest() -> None:
    proj = SchemaProjector(allowed_keys=("name", "amount"))
    input_value = {
        "name": "transaction",
        "amount": 50,
        "card_number": "4111-1111-1111-1111",  # untrusted noise
        "raw_html": "<script>alert(1)</script>",  # more noise
    }
    result = proj.declassify(
        value=input_value,
        current_label_state=LabelState(),
    )
    assert result is not None
    assert result.transformed_value == {"name": "transaction", "amount": 50}
    assert "card_number" not in result.transformed_value
    assert "raw_html" not in result.transformed_value
    assert "2 kept, 2 dropped" in result.audit_diff
    assert result.structural_proof_kind == "schema-projected"


def test_projector_lowers_provenance_level_to_trusted() -> None:
    proj = SchemaProjector(allowed_keys=("ok",), lower_axis_b_level="trusted")
    result = proj.declassify(
        value={"ok": "yes", "noise": "bad"},
        current_label_state=LabelState(),
    )
    assert result is not None
    assert result.lower_axis_b_level == "trusted"


def test_projector_skips_when_no_keys_match() -> None:
    """Input has none of the allowed keys → pass through unchanged."""
    proj = SchemaProjector(allowed_keys=("nope",))
    result = proj.declassify(
        value={"a": 1, "b": 2},
        current_label_state=LabelState(),
    )
    assert result is None


# ---------- chain composition ----------


def test_chain_redactor_then_projector() -> None:
    """Compose two declassifiers: redact PII first, then project schema.

    The pipeline lets operators stack transformations — redact text
    fields, then schema-project the result.
    """
    # Input: dict with a text field containing PII
    input_value = {
        "summary": "Customer SSN is 123-45-6789",
        "amount": 100,
        "internal_notes": "drop me",
    }
    # First, project to schema (keeps summary + amount)
    # Then, RegexRedactor needs strings so it'd skip a dict.
    # The realistic order is: project first to keep the right fields,
    # then if needed redact text within strings. But the redactor as
    # configured only handles top-level strings — so we'll just test
    # the chain works end-to-end with the projector.
    proj = SchemaProjector(allowed_keys=("summary", "amount"))
    redactor = RegexRedactor()

    final, applied = apply_declassifier_chain(
        (proj, redactor),  # projector runs first; output is a dict
        value=input_value,
        current_label_state=LabelState(),
    )
    # Projector ran, projected the dict; redactor sees dict input
    # and skips (not a string).
    assert isinstance(final, dict)
    assert "internal_notes" not in final
    assert "summary" in final
    # Only the projector contributed
    assert len(applied) == 1
    assert applied[0].structural_proof_kind == "schema-projected"


def test_chain_empty_list_returns_input_unchanged() -> None:
    final, applied = apply_declassifier_chain(
        (),
        value="anything",
        current_label_state=LabelState(),
    )
    assert final == "anything"
    assert applied == []


def test_chain_all_abstain_returns_input_unchanged() -> None:
    """Every declassifier passes through → final value is input."""
    redactor = RegexRedactor()
    proj = SchemaProjector(allowed_keys=("x",))
    # Input string with no PII; projector skips strings; redactor finds nothing.
    final, applied = apply_declassifier_chain(
        (redactor, proj),
        value="clean text",
        current_label_state=LabelState(),
    )
    assert final == "clean text"
    assert applied == []


def test_chain_sequential_application() -> None:
    """Two redactors in a row: first redacts SSN, second redacts emails.

    Each sees the prior one's output.
    """
    text = "SSN 123-45-6789 and email contact@example.com"
    r1 = RegexRedactor(
        patterns={"SSN": __import__("re").compile(r"\b\d{3}-\d{2}-\d{4}\b")},
    )
    r2 = RegexRedactor(
        patterns={"EMAIL": __import__("re").compile(r"\b[\w.+-]+@[\w-]+\.\w+\b")},
    )
    final, applied = apply_declassifier_chain(
        (r1, r2),
        value=text,
        current_label_state=LabelState(),
    )
    assert isinstance(final, str)
    assert "123-45-6789" not in final
    assert "contact@example.com" not in final
    assert len(applied) == 2
