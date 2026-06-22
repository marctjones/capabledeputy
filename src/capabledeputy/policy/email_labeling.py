"""Email labeling — IFC labels for incoming mail content (#34).

The email analogue of dynamic filesystem labeling (#5). Today the Gmail
upstream marks the whole read surface `untrusted.external` +
`confidential.personal` server-wide (a correct, fail-safe *floor*). This
module adds a declarative, **raise-only** labeler that escalates a single
message's Axis-A category from that floor based on sender / subject / body
— so a bank statement reads as `financial`, a newsletter stays
`personal`, and the egress gates become tier-sensitive.

Design + the three-layer scheme: `docs/email-labeling-design.md`.
Mirrors `policy/fs_labeling.py` deliberately (same rule shape, same
catalog-aware tier resolution, same monotone composition). The label
floor is preserved by the upstream server's `inherent_tags`; this labeler
only ever *raises*.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from capabledeputy.policy.labels import (
    CategoryTag,
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
    _category_tier,
    most_restrictive_inherit,
)


class EmailLabelRuleError(RuntimeError):
    """An email_label_rules.yaml entry is malformed. Fail-closed: a
    misconfigured labeler refuses daemon start rather than silently
    under-labeling sensitive mail."""


_PROVENANCE_LABELS: dict[str, ProvenanceLevel] = {
    "untrusted.external": ProvenanceLevel.EXTERNAL_UNTRUSTED,
    "untrusted.user_input": ProvenanceLevel.EXTERNAL_UNTRUSTED,
    "trusted.user_direct": ProvenanceLevel.PRINCIPAL_DIRECT,
}


def _label_string_to_state(label: str) -> LabelState:
    # Mirrors policy/fs_labeling._label_string_to_state (kept separate to
    # avoid coupling the two declarative labelers).
    if label in _PROVENANCE_LABELS:
        return LabelState(b=frozenset({ProvenanceTag(_PROVENANCE_LABELS[label])}))
    if label.startswith("confidential."):
        category = label.split(".", 1)[1]
        if not category:
            raise EmailLabelRuleError(f"empty category in label {label!r}")
        return LabelState(
            a=frozenset(
                {
                    CategoryTag(
                        category,
                        _category_tier(category),
                        assignment_provenance="source-declared",
                    ),
                },
            ),
        )
    raise EmailLabelRuleError(
        f"unknown label {label!r}; expected 'confidential.<category>' or "
        f"one of {sorted(_PROVENANCE_LABELS)}",
    )


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value]
    raise EmailLabelRuleError(f"expected a string or list, got {type(value).__name__}")


# Tolerant field extraction — Gmail MCP output shapes vary, so look for the
# common key spellings. Returns lowercased from/subject/body strings.
_FROM_KEYS = ("from", "sender", "From", "from_address")
_SUBJECT_KEYS = ("subject", "Subject")
_BODY_KEYS = ("body", "text", "snippet", "plain_body")


def extract_email_fields(output: Any) -> dict[str, str]:
    """Pull from / subject / body out of an upstream message result.
    Missing fields become empty strings (a rule simply won't match)."""
    if not isinstance(output, dict):
        return {"from": "", "subject": "", "body": ""}

    def _first(keys: tuple[str, ...]) -> str:
        for k in keys:
            v = output.get(k)
            if isinstance(v, str) and v:
                return v
        return ""

    return {
        "from": _first(_FROM_KEYS).lower(),
        "subject": _first(_SUBJECT_KEYS),
        "body": _first(_BODY_KEYS),
    }


@dataclass(frozen=True)
class EmailLabelRule:
    """One declarative rule. Fires when ANY configured facet matches."""

    labels: LabelState
    from_domains: tuple[str, ...] = ()
    from_addresses: tuple[str, ...] = ()
    subject_regexes: tuple[re.Pattern[str], ...] = ()
    body_regexes: tuple[re.Pattern[str], ...] = ()

    @property
    def needs_body(self) -> bool:
        return bool(self.body_regexes)

    def matches(self, fields: dict[str, str]) -> bool:
        sender = fields.get("from", "")
        for dom in self.from_domains:
            # Matches "jane@chase.com" and "Jane <jane@chase.com>".
            if ("@" + dom) in sender:
                return True
        for addr in self.from_addresses:
            if addr in sender:
                return True
        subject = fields.get("subject", "")
        for rx in self.subject_regexes:
            if rx.search(subject):
                return True
        body = fields.get("body", "")
        if body:
            for rx in self.body_regexes:
                if rx.search(body):
                    return True
        return False


@dataclass(frozen=True)
class EmailLabeler:
    rules: tuple[EmailLabelRule, ...] = ()

    @property
    def any_body_rules(self) -> bool:
        return any(r.needs_body for r in self.rules)

    def labels_for(self, fields: dict[str, str]) -> LabelState:
        if not self.rules:
            return LabelState()
        state = LabelState()
        for rule in self.rules:
            if rule.matches(fields):
                state = most_restrictive_inherit(state, rule.labels)
        return state

    def labels_for_output(self, output: Any) -> LabelState:
        """Convenience: extract fields from an upstream result then label."""
        return self.labels_for(extract_email_fields(output))

    def labels_for_message(self, output: Any, *, base: LabelState | None = None) -> LabelState:
        """Return base labels plus per-message labels, raise-only.

        This is the explicit per-message hook used by Gmail-like adapters:
        the server-level floor remains intact, and message-specific rules
        can only add more restrictive categories/provenance.
        """
        return most_restrictive_inherit(base or LabelState(), self.labels_for_output(output))


def _parse_rule(index: int, raw: Any) -> EmailLabelRule:
    if not isinstance(raw, dict):
        raise EmailLabelRuleError(f"email_label_rules[{index}] must be a mapping")
    match = raw.get("match")
    if not isinstance(match, dict):
        raise EmailLabelRuleError(f"email_label_rules[{index}].match must be a mapping")
    labels_raw = _as_list(raw.get("labels"))
    if not labels_raw:
        raise EmailLabelRuleError(f"email_label_rules[{index}] must declare at least one label")
    labels = LabelState()
    for label in labels_raw:
        labels = most_restrictive_inherit(labels, _label_string_to_state(label))

    from_domains = tuple(d.lower() for d in _as_list(match.get("from_domain")))
    from_addresses = tuple(a.lower() for a in _as_list(match.get("from_address")))
    icase = re.IGNORECASE
    try:
        subject_rx = tuple(re.compile(r, icase) for r in _as_list(match.get("subject_regex")))
        body_rx = tuple(re.compile(r, icase) for r in _as_list(match.get("body_regex")))
    except re.error as e:
        raise EmailLabelRuleError(f"email_label_rules[{index}] bad regex: {e}") from e

    if not (from_domains or from_addresses or subject_rx or body_rx):
        raise EmailLabelRuleError(
            f"email_label_rules[{index}].match needs one of from_domain / "
            "from_address / subject_regex / body_regex",
        )
    return EmailLabelRule(
        labels=labels,
        from_domains=from_domains,
        from_addresses=from_addresses,
        subject_regexes=subject_rx,
        body_regexes=body_rx,
    )


def parse_email_label_rules(raw: Any) -> EmailLabeler:
    if raw is None:
        return EmailLabeler()
    if isinstance(raw, dict):
        raw = raw.get("email_label_rules", [])
    if not isinstance(raw, list):
        raise EmailLabelRuleError("email_label_rules must be a list")
    return EmailLabeler(rules=tuple(_parse_rule(i, r) for i, r in enumerate(raw)))


def load_email_label_rules(path: Any) -> EmailLabeler:
    """Load from configs/email_label_rules.yaml. Absent ⇒ empty (off).
    Unparseable ⇒ fail-closed."""
    from pathlib import Path

    p = Path(path)
    if not p.is_file():
        return EmailLabeler()
    import yaml

    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise EmailLabelRuleError(f"email_label_rules unparseable: {p} — {e}") from e
    return parse_email_label_rules(raw)
