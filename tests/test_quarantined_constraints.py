"""Cookbook P1.7 — defense-in-depth constraints for Pattern ② output.

Tests cover:
  - Clean natural-language strings pass every constraint
  - Control characters rejected; tab/newline/CR allowed
  - DEL (0x7F) rejected
  - Bidirectional override characters rejected (Trojan Source class)
  - Zero-width joiner / non-joiner rejected
  - Base64-shaped strings rejected (40+ chars in the alphabet)
  - High-entropy non-base64 strings rejected via entropy threshold
  - Short suspicious-looking strings under threshold left alone
  - Pydantic walker handles nested dicts / lists / models
  - EXTRACTION_CONSTRAINTS_DISABLED opt-out works
  - extract() integration: a quarantined response that smuggles a
    base64 payload through a 'summary' field is rejected
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from capabledeputy.quarantined.constraints import (
    _shannon_entropy,
    assert_no_bidi_or_invisible,
    assert_no_control_chars,
    assert_not_encoded_blob,
    validate_extracted_value,
)
from capabledeputy.quarantined.extractor import ExtractionError

# --- Individual constraints ------------------------------------------------


def test_clean_text_passes_all_constraints() -> None:
    """Typical English summary — every constraint accepts it
    silently. The negative space matters: false positives on
    natural language would make Pattern ② unusable."""
    clean = (
        "Dear marc, your appointment with Dr. Lee is confirmed for "
        "Tuesday at 3pm. Please bring your insurance card."
    )
    assert_no_control_chars(clean, "summary")
    assert_no_bidi_or_invisible(clean, "summary")
    assert_not_encoded_blob(clean, "summary")


def test_control_chars_rejected() -> None:
    with pytest.raises(ExtractionError, match="control character"):
        assert_no_control_chars("normal text \x07 with bell", "subject")
    with pytest.raises(ExtractionError, match="control character"):
        assert_no_control_chars("escape \x1b sequence", "subject")


def test_del_rejected() -> None:
    with pytest.raises(ExtractionError, match="DEL"):
        assert_no_control_chars("trailing del\x7f", "subject")


def test_whitespace_controls_allowed() -> None:
    """\\t, \\n, \\r are legitimate text — must NOT be rejected."""
    assert_no_control_chars("line one\nline two", "body")
    assert_no_control_chars("col1\tcol2", "body")
    assert_no_control_chars("crlf\r\nstyle", "body")


def test_bidi_override_rejected() -> None:
    """U+202E (RIGHT-TO-LEFT OVERRIDE) is the Trojan-Source class
    canonical example. Embedding it makes a string render
    differently than its literal bytes — must always reject in
    extracted output."""
    trojan = "innocent‮evil"
    with pytest.raises(ExtractionError, match="bidi/invisible"):
        assert_no_bidi_or_invisible(trojan, "summary")


def test_zero_width_joiner_rejected() -> None:
    """U+200C (ZWNJ) / U+200D (ZWJ) can disguise visually-identical
    text from distinct codepoints. Reject."""
    with pytest.raises(ExtractionError, match="bidi/invisible"):
        assert_no_bidi_or_invisible("ev‌il", "summary")
    with pytest.raises(ExtractionError, match="bidi/invisible"):
        assert_no_bidi_or_invisible("ev‍il", "summary")


def test_base64_shaped_string_rejected() -> None:
    """A 40-char string of [A-Za-z0-9+/=] is almost certainly
    encoded data, not natural language."""
    blob = "SGVsbG8gd29ybGQhIFRoaXMgaXMgYSB0ZXN0IHN0cmluZyB0aGF0IGlzIGxvbmcuLi4="
    with pytest.raises(ExtractionError, match="base64 shape"):
        assert_not_encoded_blob(blob, "summary")


def test_high_entropy_string_rejected() -> None:
    """A 60-char string with high Shannon entropy (>5.0 bits/char,
    ruling out hex which caps at log2(16)=4) is treated as encoded
    data even when not base64-shaped. Built from a wide character
    spread including symbols not in the base64 alphabet."""
    # Each character unique → entropy log2(N) bits/char. 50 unique
    # characters give ~5.6 bits/char, well above the 5.0 threshold.
    blob = "Abc!1Def@2Ghi#3Jkl$4Mno%5Pqr^6Stu&7Vwx*8Yz(9?{[<>~\\."
    assert len(set(blob)) >= 50
    assert _shannon_entropy(blob) >= 5.0  # sanity-check the fixture
    # Includes non-base64 chars (!#%&@*^_?:;<>~={}|[]), so the
    # base64-shape regex doesn't match; the high-entropy check
    # catches it instead.
    with pytest.raises(ExtractionError, match="entropy"):
        assert_not_encoded_blob(blob, "summary")


def test_short_strings_skip_entropy_check() -> None:
    """A short identifier or code (<40 chars) is allowed regardless
    of entropy. Empirical: natural-language summaries don't trip on
    UUID-like substrings if those substrings stand alone."""
    short_uuid = "550e8400-e29b-41d4"  # 18 chars
    assert_not_encoded_blob(short_uuid, "id")  # no raise


def test_low_entropy_long_text_allowed() -> None:
    """Long natural-language strings stay well below the entropy
    threshold; the constraint must accept them."""
    natural = "The quick brown fox jumps over the lazy dog. " * 5  # 225 chars, English entropy ~4.0
    assert_not_encoded_blob(natural, "summary")


# --- Walker + validate_extracted_value ------------------------------------


class _CleanModel(BaseModel):
    sender: str = Field(max_length=80)
    summary: str = Field(max_length=200)
    n_emails: int = Field(ge=0, le=100)


class _NestedModel(BaseModel):
    parent_name: str = Field(max_length=80)
    children: list[str] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)


def test_validate_extracted_value_passes_clean_model() -> None:
    model = _CleanModel(
        sender="Dr. Lee",
        summary="Your appointment is Tuesday at 3pm.",
        n_emails=2,
    )
    validate_extracted_value(model)  # no raise


def test_validate_extracted_value_rejects_in_any_field() -> None:
    bad = _CleanModel(
        sender="Dr. Lee",
        summary="evil‮innocent",  # bidi override
        n_emails=2,
    )
    with pytest.raises(ExtractionError, match="bidi/invisible"):
        validate_extracted_value(bad)


def test_validate_extracted_value_walks_lists() -> None:
    model = _NestedModel(
        parent_name="parent",
        children=["fine", "also fine", "evil‮innocent"],
    )
    with pytest.raises(ExtractionError, match="bidi/invisible"):
        validate_extracted_value(model)


def test_validate_extracted_value_walks_dicts() -> None:
    model = _NestedModel(
        parent_name="parent",
        metadata={"label": "ok", "smuggled": "\x07bell"},
    )
    with pytest.raises(ExtractionError, match="control character"):
        validate_extracted_value(model)


def test_extraction_constraints_disabled_opt_out() -> None:
    """A schema that genuinely needs unconstrained text can opt
    out via the class attribute. Confirm the validator respects
    the flag."""
    from typing import ClassVar

    class _UnconstrainedModel(BaseModel):
        body: str = Field(max_length=10_000)

        EXTRACTION_CONSTRAINTS_DISABLED: ClassVar[bool] = True

    model = _UnconstrainedModel(body="evil‮content")
    validate_extracted_value(model)  # no raise


# --- extract() integration ------------------------------------------------


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_extract_rejects_smuggled_base64_payload() -> None:
    """End-to-end: a quarantined LLM that returns a JSON object
    whose string field is a base64-shaped blob is rejected at
    validate_extracted_value, surfacing as ExtractionError to the
    caller. Closes the cookbook P1.7 gap."""
    from capabledeputy.llm.fake import FakeLLMClient
    from capabledeputy.llm.types import FinishReason, LLMResponse
    from capabledeputy.quarantined.extractor import extract
    from capabledeputy.quarantined.schemas import get_schema

    # Reuse a real schema with a string field; the FakeLLM returns
    # output that passes Pydantic but smuggles a payload.
    schema_name = "DailyBriefing"
    # Confirm the schema exists; if not, this test is a no-op.
    get_schema(schema_name)

    smuggled = "SGVsbG8gd29ybGQhIFRoaXMgaXMgYSB0ZXN0IHN0cmluZyBzbXVnZ2xlZA=="
    response = LLMResponse(
        content=(
            '{"date": "2026-06-04", "n_calendar_events": 1, '
            '"n_unread_emails": 0, '
            f'"top_priority": "{smuggled}", '
            '"suggested_focus": "ok"}'
        ),
        finish_reason=FinishReason.STOP,
    )
    fake = FakeLLMClient([response])
    with pytest.raises(ExtractionError, match="base64 shape"):
        await extract(fake, schema_name, "any labeled source")
