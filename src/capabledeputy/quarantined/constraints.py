"""Defense-in-depth constraints for quarantined-LLM output.

Pattern ② declassifies labeled data through a Pydantic schema — the
quarantined LLM emits a structured object, and the planner sees only
the structured fields. If the quarantined LLM is itself compromised
by a clever injection in the source content, its emitted fields are
still bounded by Pydantic length/type constraints, but a determined
adversary can smuggle covert data through 'valid' string fields:

  - control characters that some downstream renderer interprets
  - bidirectional / zero-width unicode that visually disguises
    content (homoglyph-style attacks)
  - high-entropy strings that look like packed/encoded payloads
    (base64 of an exfiltrated value smuggled through a `subject`
    field, for instance)

This module is the constraint layer. Each function takes a string
value + a field name (for error context) and raises ExtractionError
on violation. The dispatcher's `extract()` runs the default set on
every string field of a validated model after Pydantic accepts it;
violations bubble up the same way schema failures do.

The constraints are intentionally aggressive — Pattern ② is for
extracting STRUCTURED facts (sender, date, amount, summary) from
adversarial sources, NOT for round-tripping arbitrary content. A
schema that genuinely needs unconstrained text (rare) can opt out
via the schema-level `EXTRACTION_CONSTRAINTS_DISABLED = True` class
attribute.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable

from capabledeputy.quarantined.extractor import ExtractionError

# Characters allowed in a 'tab+newline+CR + printable' field. We
# explicitly carve out the three whitespace controls that legitimate
# text uses; everything else in the 0x00 to 0x1F range is rejected as a
# potential covert channel for a downstream renderer.
_ALLOWED_CONTROL_CHARS: frozenset[str] = frozenset({"\t", "\n", "\r"})

# Unicode categories that indicate bidirectional or invisible
# characters often used in visual-spoofing attacks. Cf MITRE T1027
# (obfuscation) + the "Trojan Source" CVE-2021-42574 class.
_BIDI_AND_INVISIBLE_CATEGORIES: frozenset[str] = frozenset(
    {
        # Format characters — includes LRM/RLM/PDI and the LRO/RLO
        # explicit-override codepoints that flip text rendering
        # direction (Trojan Source). Also covers ZWJ/ZWNJ/ZWSP.
        "Cf",
        # Cs surrogates / Co private-use — never expected in
        # extracted text; their presence is suspicious.
        "Cs",
        "Co",
    },
)

# Shannon entropy threshold above which a string is treated as
# "looks like encoded data" rather than natural language. Empirical
# tuning: typical English sentences score 3.0-4.5 bits/char;
# base64 / URL-encoded blobs score ~5.5-6.0; random uniform bytes
# approach 8.0. 5.0 catches the obvious smuggling cases while
# leaving normal text alone.
_ENTROPY_BITS_THRESHOLD: float = 5.0

# Minimum length below which we don't bother entropy-checking — a
# 12-character UUID-ish string can legitimately appear in many
# fields and would false-positive on entropy alone.
_ENTROPY_CHECK_MIN_LENGTH: int = 40

# Regex matching strings that look like base64 (lots of [A-Za-z0-9+/=]
# and the right length divisibility). Tighter than entropy: a
# 200-char string that's all base64 alphabet is almost certainly
# encoded data, regardless of entropy.
_BASE64_PATTERN = re.compile(r"^[A-Za-z0-9+/=]{40,}$")


def assert_no_control_chars(value: str, field_name: str) -> None:
    """Raise ExtractionError if `value` contains control characters
    other than \\t, \\n, \\r. Defends against downstream renderers
    that interpret ANSI escapes, terminal control codes, or other
    bytes embedded in 'plain text'."""
    for ch in value:
        code = ord(ch)
        if code < 0x20 and ch not in _ALLOWED_CONTROL_CHARS:
            raise ExtractionError(
                f"quarantined-LLM output field {field_name!r} contains "
                f"control character U+{code:04X} — defense-in-depth "
                "rejection (possible covert channel)",
            )
        if code == 0x7F:
            raise ExtractionError(
                f"quarantined-LLM output field {field_name!r} contains DEL character — rejected",
            )


def assert_no_bidi_or_invisible(value: str, field_name: str) -> None:
    """Raise ExtractionError on bidirectional or invisible Unicode.
    Catches the Trojan-Source class (CVE-2021-42574) where direction-
    override codepoints disguise the literal content of a string."""
    for ch in value:
        cat = unicodedata.category(ch)
        if cat in _BIDI_AND_INVISIBLE_CATEGORIES:
            raise ExtractionError(
                f"quarantined-LLM output field {field_name!r} contains "
                f"bidi/invisible character U+{ord(ch):04X} (category "
                f"{cat}) — defense-in-depth rejection (Trojan-Source class)",
            )


def assert_not_encoded_blob(value: str, field_name: str) -> None:
    """Raise ExtractionError on strings that look like base64 /
    high-entropy encoded data. Defends against a compromised
    quarantined LLM smuggling an exfiltrated payload through what
    should be a natural-language field.

    Two checks compose: a base64-shape regex (cheap + specific) and
    a Shannon-entropy threshold (catches non-base64 packed data).
    Short strings skip both — a 12-char identifier can legitimately
    appear in many fields."""
    if len(value) < _ENTROPY_CHECK_MIN_LENGTH:
        return
    if _BASE64_PATTERN.match(value):
        raise ExtractionError(
            f"quarantined-LLM output field {field_name!r} matches base64 "
            f"shape ({len(value)} chars of A-Za-z0-9+/=) — defense-in-depth "
            "rejection (likely encoded payload smuggling)",
        )
    if _shannon_entropy(value) >= _ENTROPY_BITS_THRESHOLD:
        raise ExtractionError(
            f"quarantined-LLM output field {field_name!r} has high "
            f"Shannon entropy ({_shannon_entropy(value):.2f} bits/char) "
            "— defense-in-depth rejection (looks like encoded data)",
        )


def _shannon_entropy(s: str) -> float:
    """Bits-per-character Shannon entropy. English text scores
    3.0-4.5; base64 ~5.5-6.0; random bytes ~8.0."""
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    total = len(s)
    return -sum((count / total) * math.log2(count / total) for count in counts.values())


_DEFAULT_CONSTRAINTS: tuple[
    tuple[str, callable[[str, str], None]], ...  # type: ignore[name-defined]
] = (
    ("no-control-chars", assert_no_control_chars),
    ("no-bidi-or-invisible", assert_no_bidi_or_invisible),
    ("not-encoded-blob", assert_not_encoded_blob),
)


def _iter_string_fields(value: object, prefix: str = "") -> Iterable[tuple[str, str]]:
    """Walk a Pydantic model (or nested dict/list/model) and yield
    (qualified_field_name, string_value) for every string leaf. Used
    to apply the default constraints across an arbitrary schema."""
    from pydantic import BaseModel

    if isinstance(value, BaseModel):
        for name, field_value in value.model_dump().items():
            yield from _iter_string_fields(
                field_value,
                prefix=f"{prefix}.{name}" if prefix else name,
            )
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _iter_string_fields(v, prefix=f"{prefix}[{k!r}]")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _iter_string_fields(v, prefix=f"{prefix}[{i}]")
    elif isinstance(value, str):
        yield (prefix or "<root>", value)


def validate_extracted_value(model: object) -> None:
    """Apply the default constraint set to every string field of a
    validated Pydantic model. Raise ExtractionError on first
    violation; the dispatcher's caller already handles
    ExtractionError.

    Schemas may opt out wholesale by setting the class attribute
    `EXTRACTION_CONSTRAINTS_DISABLED = True`. This is intended for
    rare cases where the schema genuinely needs unconstrained
    text — review carefully before using.
    """
    disabled = getattr(type(model), "EXTRACTION_CONSTRAINTS_DISABLED", False)
    if disabled:
        return
    for field_name, value in _iter_string_fields(model):
        for _label, check in _DEFAULT_CONSTRAINTS:
            check(value, field_name)
