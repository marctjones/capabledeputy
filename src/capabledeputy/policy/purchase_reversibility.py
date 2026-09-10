"""Per-call reversibility resolution for `purchase.queue` (FR-037).

`purchase.queue`'s `ToolDefinition.default_reversibility` is a static,
fail-closed `irreversible/external` — correct as a floor, since most
purchases (concert tickets, custom builds, one-off services) really
can't be undone. But `configs/rules.yaml`'s
`purchases-under-threshold-auto` rule exists specifically to auto-
approve the *other* case: a small purchase from a vendor with a real,
relied-on return/cancellation policy (its own comment: "the rule fires
only when the deterministic reversibility resolver returns
'reversible/system'"). Without a resolver, that rule can never see
anything but the static default and is dead code against real
dispatch — `reversibility_gate()` in `policy/assurance.py` maps
irreversible + non-communication effects straight to a hard DENY that
always outranks a v2 AUTO in `engine._compose_with_reversibility`.

This module is that resolver: an operator-declared, vendor-pattern-
keyed allowlist (`configs/purchase_reversibility.yaml`), loaded the
same way `fs_labeling.py` loads `fs_label_rules.yaml` — absent file ⇒
empty policy (no vendor gets special treatment, the static irreversible
floor holds everywhere, matching today's behavior); unparseable file ⇒
fail-closed (Principle VI).

Deliberately NOT reusing `SourceLocationLabelBinding` /
`policy/bindings.py`: bindings resolve *canonical URI-shaped
locations* (`file://`, `s3://`, ...) via `_target_uses_binding_namespace`,
and purchase.queue's target is a bare vendor name ("amazon"), not a
location. Forcing a vendor allowlist through the location-binding
machinery would mean inventing a fake URI scheme for a concept
(who will honor a return?) that has nothing to do with data taint.
A small dedicated loader, matched on `ToolDefinition.reversibility_resolver`
(the same per-call-hook shape as the existing `source_label_lookup`),
is the narrower, more honest fit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from capabledeputy.policy.reversibility import ReversibilityLabel, compose_reversibility


class PurchaseReversibilityError(RuntimeError):
    """A purchase_reversibility.yaml entry is malformed. Fail-closed
    per Principle VI: a misconfigured policy refuses daemon start
    rather than silently mis-resolving a vendor's reversibility."""


@dataclass(frozen=True)
class VendorReversibilityRule:
    """One operator-declared vendor entry. Matches when the purchase's
    `vendor` arg matches ANY of `vendor_globs` (case-insensitive
    `fnmatch`, so "Amazon" matches a declared "amazon*")."""

    vendor_globs: tuple[str, ...]
    label: ReversibilityLabel

    def matches(self, vendor: str) -> bool:
        folded = vendor.lower()
        return any(fnmatch(folded, glob.lower()) for glob in self.vendor_globs)


@dataclass(frozen=True)
class PurchaseReversibilityPolicy:
    """Applies the loaded vendor rules to a `purchase.queue` call's
    `vendor` arg. Empty rule set ⇒ no match, ever (the tool's static
    `default_reversibility` floor holds)."""

    rules: tuple[VendorReversibilityRule, ...] = field(default_factory=tuple)

    def resolve_for_vendor(self, vendor: str) -> ReversibilityLabel | None:
        if not vendor:
            return None
        matched = [rule.label for rule in self.rules if rule.matches(vendor)]
        if not matched:
            return None
        # Most-restrictive composition (FR-037): if two declared
        # entries both match a vendor string, the stricter one wins —
        # an operator narrowing an existing entry never silently
        # loosens it via a second, broader one.
        return compose_reversibility(*matched)


def _as_globs(value: Any, *, index: int) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        if not value:
            raise PurchaseReversibilityError(
                f"purchase_reversibility[{index}].match.vendor_glob must not be empty",
            )
        return tuple(str(v) for v in value)
    raise PurchaseReversibilityError(
        f"purchase_reversibility[{index}].match.vendor_glob must be a string or list",
    )


def _parse_rule(index: int, raw: Any) -> VendorReversibilityRule:
    if not isinstance(raw, dict):
        raise PurchaseReversibilityError(f"purchase_reversibility[{index}] must be a mapping")
    match = raw.get("match")
    if not isinstance(match, dict) or "vendor_glob" not in match:
        raise PurchaseReversibilityError(
            f"purchase_reversibility[{index}].match.vendor_glob is required",
        )
    globs = _as_globs(match["vendor_glob"], index=index)

    rev = raw.get("reversibility")
    if not isinstance(rev, dict):
        raise PurchaseReversibilityError(
            f"purchase_reversibility[{index}].reversibility must be a mapping "
            "with 'degree' and 'agent'",
        )
    try:
        label = ReversibilityLabel.from_dict(rev)
    except (KeyError, ValueError) as e:
        raise PurchaseReversibilityError(
            f"purchase_reversibility[{index}].reversibility invalid: {e}",
        ) from e

    return VendorReversibilityRule(vendor_globs=globs, label=label)


def parse_purchase_reversibility_policy(raw: Any) -> PurchaseReversibilityPolicy:
    """Build a PurchaseReversibilityPolicy from already-parsed YAML (a
    list of rules, or a mapping with a top-level
    `purchase_reversibility:` key)."""
    if raw is None:
        return PurchaseReversibilityPolicy()
    if isinstance(raw, dict):
        raw = raw.get("purchase_reversibility", [])
    if not isinstance(raw, list):
        raise PurchaseReversibilityError("purchase_reversibility must be a list")
    return PurchaseReversibilityPolicy(
        rules=tuple(_parse_rule(i, r) for i, r in enumerate(raw)),
    )


def load_purchase_reversibility_policy(path: Path) -> PurchaseReversibilityPolicy:
    """Load the policy from `configs/purchase_reversibility.yaml`.
    Absent file ⇒ empty policy (every vendor falls back to the tool's
    static irreversible/external floor — today's behavior, unchanged).
    Unparseable ⇒ fail-closed."""
    if not path.is_file():
        return PurchaseReversibilityPolicy()
    import yaml

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise PurchaseReversibilityError(
            f"purchase_reversibility unparseable: {path} — {e}",
        ) from e
    return parse_purchase_reversibility_policy(raw)
