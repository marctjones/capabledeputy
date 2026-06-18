"""DeclassifyingTransformer port (spec 004 P0).

Third programmatic primitive. Transforms a value AND lowers session
labels, with a structural-proof field so auditors can verify the
declassification rationale.

The model of a declassifier is:
  (value, current_axes) -> (transformed_value, new_axes, proof)

Where:
  - transformed_value REPLACES the input value (the agent never sees
    the original after declassification)
  - new_axes is the lowered label set (must be a SUBSET of input
    axes — declassifiers cannot raise; raising is RaiseOnlyInspector's job)
  - proof is a structural attestation: "regex-redacted N SSNs",
    "projected to schema X", "passed quarantined classifier Y"

Use cases:
  - RegexRedactor:        SSNs / credit cards / phone numbers → [REDACTED]
                          ⇒ lower pii.restricted → pii.sensitive
  - SchemaProjector:      arbitrary input → only the schema's allowed
                          fields ⇒ lower untrusted.external → trusted
  - QuarantinedClassifier: pass through a separate LLM that emits
                          structured JSON; only the JSON returns ⇒
                          lower untrusted → trusted

Compared to the other two primitives:
  - RaiseOnlyInspector: at INGEST; can only raise
  - DecisionInspector:  at DECISION; relax/tighten outcomes
  - DeclassifyingTransformer: at TRANSFORM; lower labels + replace value

Composition is sequential: multiple transformers run in operator-declared
order; each sees the previous one's output (and adjusted labels). This
lets operators stack transformations (e.g., first regex-redact, then
project to schema).

Contract: pure function. No I/O. No side effects. The structural-proof
must be auditable + reproducible from the input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from capabledeputy.policy.labels import LabelState, ProvenanceLevel
from capabledeputy.policy.tiers import Tier
from capabledeputy.policy.tiers import compare as compare_tier


@dataclass(frozen=True)
class DeclassifyResult:
    """Output of one declassifier application.

    Attributes:
        transformed_value: The value the agent will see (replaces input).
        lower_axis_a_categories: Categories to LOWER. Each entry is
                                 {category, to_tier} where to_tier is the
                                 new (strictly lower) tier.
        lower_axis_b_level: New axis_b (provenance) level, must be a
                            lower restriction than input. None = unchanged.
        audit_diff: One-line summary for the audit log
                    ("3 SSNs redacted", "5 fields projected").
        structural_proof_kind: Tag identifying the proof type
                              ("regex-redacted", "schema-projected",
                               "quarantined-extract"). Auditor uses this
                              to know HOW to verify the declassification.
    """

    transformed_value: Any
    lower_axis_a_categories: tuple[dict[str, str], ...] = field(default_factory=tuple)
    lower_axis_b_level: str | None = None
    audit_diff: str = ""
    structural_proof_kind: str = ""


class DeclassifyingTransformer(Protocol):
    """Declassifier contract.

    Implementations declare a `name` for audit attribution and a
    `declassify()` method called by the runtime in operator-declared
    order. Each sees the previous transformer's output. Returns None
    to pass through unchanged.

    The transformed_value REPLACES the input value the agent sees.
    Composition: outputs feed into the next transformer's input.
    """

    name: str

    def declassify(
        self,
        *,
        value: Any,
        current_label_state: LabelState,
        context: dict[str, Any] | None = None,
    ) -> DeclassifyResult | None:
        """Transform value (optionally) and lower labels (optionally).

        Args:
            value: Current value (from upstream tool or prior declassifier).
            current_label_state: Session's current four-axis LabelState.
            context: Operator-supplied context (e.g., destination URI).

        Returns:
            DeclassifyResult to apply, or None to pass through unchanged.
        """
        ...


class DeclassifierValidationError(RuntimeError):
    """A declassifier result is missing auditable proof or attempts an
    invalid label lowering. The caller must treat the transform as failed."""


_PROVENANCE_RANK: dict[ProvenanceLevel, int] = {
    ProvenanceLevel.PRINCIPAL_DIRECT: 0,
    ProvenanceLevel.SYSTEM_INTERNAL: 1,
    ProvenanceLevel.EXTERNAL_UNTRUSTED: 2,
}


def validate_declassify_result(result: DeclassifyResult, current_state: LabelState) -> None:
    """Validate a declassifier's proof and requested label lowering."""
    if not result.structural_proof_kind.strip():
        raise DeclassifierValidationError("declassifier result missing structural_proof_kind")
    if not result.audit_diff.strip():
        raise DeclassifierValidationError("declassifier result missing audit_diff")

    current_by_category = {tag.category: tag for tag in current_state.a}
    for entry in result.lower_axis_a_categories:
        category = str(entry.get("category", ""))
        if not category:
            raise DeclassifierValidationError("declassifier category lowering missing category")
        current = current_by_category.get(category)
        if current is None:
            raise DeclassifierValidationError(
                f"declassifier attempted to lower absent category {category!r}",
            )
        try:
            to_tier = Tier(str(entry.get("to_tier", "")))
        except ValueError as exc:
            raise DeclassifierValidationError(
                f"declassifier returned unknown target tier {entry.get('to_tier')!r}",
            ) from exc
        if compare_tier(to_tier, current.tier) >= 0:
            raise DeclassifierValidationError(
                f"declassifier target tier {to_tier.value!r} does not lower "
                f"current tier {current.tier.value!r} for {category!r}",
            )

    if result.lower_axis_b_level is not None:
        try:
            target = ProvenanceLevel(result.lower_axis_b_level)
        except ValueError as exc:
            raise DeclassifierValidationError(
                f"declassifier returned unknown provenance level {result.lower_axis_b_level!r}",
            ) from exc
        if not current_state.b:
            raise DeclassifierValidationError(
                "declassifier attempted provenance lowering with no provenance labels",
            )
        lowers_existing_label = any(
            _PROVENANCE_RANK[target] < _PROVENANCE_RANK[tag.level] for tag in current_state.b
        )
        if not lowers_existing_label:
            raise DeclassifierValidationError(
                f"declassifier target provenance {target.value!r} does not lower current labels",
            )


def apply_declassifier_chain(
    declassifiers: tuple[DeclassifyingTransformer, ...],
    value: Any,
    current_label_state: LabelState,
    context: dict[str, Any] | None = None,
) -> tuple[Any, list[DeclassifyResult]]:
    """Apply declassifiers in operator-declared order.

    Each declassifier sees the previous transformer's output. Returns
    the final transformed value + the list of every applied result
    (for audit). Declassifiers returning None are skipped (no-op).

    Pure function — does not mutate inputs; declassifiers are by
    contract pure too.
    """
    current_value = value
    applied: list[DeclassifyResult] = []
    current_state = current_label_state
    for transformer in declassifiers:
        result = transformer.declassify(
            value=current_value,
            current_label_state=current_state,
            context=context,
        )
        if result is None:
            continue
        validate_declassify_result(result, current_state)
        applied.append(result)
        current_value = result.transformed_value
        # Note: actual label lowering is applied by host code (the
        # chokepoint integration), not here — this function returns
        # the desired deltas for the host to apply with monotone-aware
        # composition. The transformer pipeline only transforms values.
    return current_value, applied
