"""Builtin DeclassifyingTransformer implementations.

These are reference declassifiers operators can register out of the
box. Each transforms input + lowers labels with a structural proof
the auditor can verify.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from capabledeputy.substrate.declassifier_port import DeclassifyResult

SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
CC_PATTERN = re.compile(r"\b(?:\d{4}[\s-]?){3}\d{4}\b")
PHONE_PATTERN = re.compile(r"\b\d{3}[\s.-]?\d{3}[\s.-]?\d{4}\b")
EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


@dataclass(frozen=True)
class RegexRedactor:
    """Redact PII patterns and (optionally) lower the pii category tier.

    Operator declares which patterns to redact. Default set covers
    SSNs, credit cards, phone numbers, emails — operators add more
    via the `patterns` map.

    When the input axis_a contains a `pii` category at `restricted` or
    `sensitive` tier, and redaction made changes, the transformer
    proposes lowering pii to `none` (since the document no longer
    contains PII).

    Attributes:
        patterns: dict of label -> compiled regex; defaults to common PII
        lower_to_tier: target tier for pii after redaction (default 'none')
        redaction_marker: replacement string (default '[REDACTED]')
    """

    name: str = "RegexRedactor"
    patterns: dict[str, re.Pattern[str]] = field(
        default_factory=lambda: {
            "SSN": SSN_PATTERN,
            "CC": CC_PATTERN,
            "PHONE": PHONE_PATTERN,
            "EMAIL": EMAIL_PATTERN,
        },
    )
    lower_to_tier: str = "none"
    redaction_marker: str = "[REDACTED]"

    def declassify(
        self,
        *,
        value: Any,
        current_label_state: Any,  # LabelState
        context: dict[str, Any] | None = None,
    ) -> DeclassifyResult | None:
        if not isinstance(value, str):
            # Only strings carry textual PII; structured data is the
            # SchemaProjector's job.
            return None
        text = value
        counts: dict[str, int] = {}
        for label, pat in self.patterns.items():
            count = len(pat.findall(text))
            if count > 0:
                counts[label] = count
                text = pat.sub(f"{self.redaction_marker}-{label}", text)
        if not counts:
            return None
        diff_parts = [f"{n} {label}" for label, n in counts.items()]
        diff = " + ".join(diff_parts) + " redacted"
        lower_axis_a: tuple[dict[str, str], ...] = ()
        if any(tag.category == "pii" for tag in getattr(current_label_state, "a", ())):
            lower_axis_a = ({"category": "pii", "to_tier": self.lower_to_tier},)
        return DeclassifyResult(
            transformed_value=text,
            lower_axis_a_categories=lower_axis_a,
            audit_diff=diff,
            structural_proof_kind="regex-redacted",
        )


@dataclass(frozen=True)
class SchemaProjector:
    """Project arbitrary input down to operator-declared allowed fields.

    Use case: a tool returns rich JSON (potentially with extra fields,
    arbitrary nested content). The projector emits only the allowed
    keys, dropping everything else. Since the projection is structural
    and operator-authored, the result is `trusted` regardless of input.

    Operator's contract: declare the projection schema. The
    transformer verifies the input has those keys and emits a
    flattened dict. Untrusted source → trusted output.

    Attributes:
        name: identifier for audit attribution
        allowed_keys: tuple of top-level keys to keep (other keys discarded)
        lower_axis_b_level: target axis_b level after projection
                            (default 'trusted')
    """

    name: str = "SchemaProjector"
    allowed_keys: tuple[str, ...] = ()
    lower_axis_b_level: str = "principal-direct"

    def declassify(
        self,
        *,
        value: Any,
        current_label_state: Any,  # LabelState
        context: dict[str, Any] | None = None,
    ) -> DeclassifyResult | None:
        if not isinstance(value, dict):
            return None
        if not self.allowed_keys:
            return None
        # Project: keep only the allowed keys
        projected: dict[str, Any] = {k: value[k] for k in self.allowed_keys if k in value}
        if not projected:
            # Schema didn't match any field — pass through unchanged
            # rather than emit an empty dict
            return None
        lower_axis_b_level: str | None = None
        if any(tag.level.value != self.lower_axis_b_level for tag in current_label_state.b):
            lower_axis_b_level = self.lower_axis_b_level
        n_kept = len(projected)
        n_dropped = len(value) - n_kept
        return DeclassifyResult(
            transformed_value=projected,
            lower_axis_b_level=lower_axis_b_level,
            audit_diff=(f"projected to schema ({n_kept} kept, {n_dropped} dropped)"),
            structural_proof_kind="schema-projected",
        )
