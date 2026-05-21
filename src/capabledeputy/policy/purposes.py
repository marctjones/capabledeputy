"""Purpose registry (003 US3 / FR-009 / FR-046).

A Purpose declares the data categories a session pursuing it is
allowed to *read*. Sessions are spawned with a `purpose_handle`;
SessionGraph.new refuses any candidate capability whose effect would
read an inadmissible category for that purpose (FR-009).
This is structural prevention — the inadmissible category never
enters the session in the first place, so it cannot leak.

Purposes are operator-declared in configs/purposes.yaml and read-only
to the AI. The special `unset` purpose admits no consequential
effects: a session without an explicit purpose handle cannot do
anything that would touch a labeled category (FR-046 fail-closed).

Admissibility model:
  - `admissible_categories: [..]` is a whitelist. If declared, only
    these categories are admissible.
  - `inadmissible_categories: [..]` is a blacklist. Declared
    categories are refused regardless of any whitelist.
  - If both are declared, blacklist wins (most-restrictive, FR-024).
  - If neither is declared, the purpose admits NO category (the
    safe default — a purpose that doesn't enumerate is conservative).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from capabledeputy.policy.bindings import SourceLocationLabelBinding
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.tiers import Tier


class PurposeError(RuntimeError):
    """purposes.yaml is malformed or unparseable. Fail-closed per
    Principle VI."""


# The sentinel value used by Session.purpose_handle when no purpose
# was declared at spawn. Defined here so callers don't hardcode the
# literal in admissibility checks scattered across the codebase.
UNSET_PURPOSE_HANDLE = "unset"


@dataclass(frozen=True)
class Purpose:
    purpose_id: str
    label: str = ""
    admissible_categories: frozenset[str] = field(default_factory=frozenset)
    inadmissible_categories: frozenset[str] = field(default_factory=frozenset)
    recommended_pattern: str | None = None
    # Capabilities a session spawned with this purpose is born with.
    # The /grant flow is still required for any cap NOT in this list.
    # Operators use this to encode "every research-purpose session
    # automatically gets fs.read on ~/research/**" without making the
    # user grant by hand every time.
    default_capabilities: tuple[Capability, ...] = field(default_factory=tuple)
    # Per-purpose path/URI label bindings. At chokepoint time, these
    # are composed with the global BindingSet — specificity-based
    # resolution (most-specific-wins) means a purpose's narrow paths
    # override broader global rules without explicit precedence logic.
    # Use case: "research sessions see ~/research/** as research/none;
    # tax-prep sessions see ~/finance/tax-2026/** as finance/restricted."
    bindings: tuple[SourceLocationLabelBinding, ...] = field(default_factory=tuple)

    def admits(self, category: str) -> bool:
        """True iff this purpose admits the given data category for
        read access. Blacklist wins; an empty whitelist admits
        nothing (conservative default)."""
        if category in self.inadmissible_categories:
            return False
        if not self.admissible_categories:
            return False
        return category in self.admissible_categories


@dataclass(frozen=True)
class Purposes:
    """Loaded purpose registry. Look up by purpose_id; unknown handles
    (including the special `unset`) admit nothing."""

    purposes: dict[str, Purpose]

    def get(self, purpose_id: str) -> Purpose | None:
        return self.purposes.get(purpose_id)

    def admits(self, purpose_id: str, category: str) -> bool:
        """True iff the registered purpose admits the category. An
        unregistered purpose_id (including `unset`) admits nothing,
        so this is fail-closed (FR-046)."""
        purpose = self.purposes.get(purpose_id)
        if purpose is None:
            return False
        return purpose.admits(category)


def load(path: Path) -> Purposes:
    """Load configs/purposes.yaml. Missing file ⇒ PurposeError.
    Empty `purposes:` permitted — yields an empty registry that
    admits nothing for any handle (FR-046 fail-closed)."""
    if not path.is_file():
        raise PurposeError(f"purposes config missing: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise PurposeError(f"unparseable: {path} — {e}") from e
    if data is None:
        return Purposes(purposes={})
    raw = data.get("purposes") or []
    if not isinstance(raw, list):
        raise PurposeError(f"'purposes' must be a list: {path}")
    out: dict[str, Purpose] = {}
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise PurposeError(f"purposes[{i}] is not an object")
        try:
            pid = str(item["purpose_id"])
        except KeyError:
            raise PurposeError(f"purposes[{i}] missing 'purpose_id'") from None
        if pid in out:
            raise PurposeError(f"purposes[{i}] duplicate purpose_id: {pid!r}")
        admissible_raw = item.get("admissible_categories") or []
        inadmissible_raw = item.get("inadmissible_categories") or []
        if not isinstance(admissible_raw, list):
            raise PurposeError(
                f"purposes[{i}].admissible_categories must be a list",
            )
        if not isinstance(inadmissible_raw, list):
            raise PurposeError(
                f"purposes[{i}].inadmissible_categories must be a list",
            )
        caps_raw = item.get("default_capabilities") or []
        if not isinstance(caps_raw, list):
            raise PurposeError(
                f"purposes[{i}].default_capabilities must be a list",
            )
        default_capabilities = tuple(
            _parse_default_capability(cap_raw, i, j) for j, cap_raw in enumerate(caps_raw)
        )
        bindings_raw = item.get("bindings") or []
        if not isinstance(bindings_raw, list):
            raise PurposeError(
                f"purposes[{i}].bindings must be a list",
            )
        bindings = tuple(
            _parse_purpose_binding(b_raw, i, j) for j, b_raw in enumerate(bindings_raw)
        )
        out[pid] = Purpose(
            purpose_id=pid,
            label=str(item.get("label", "")),
            admissible_categories=frozenset(str(c) for c in admissible_raw),
            inadmissible_categories=frozenset(str(c) for c in inadmissible_raw),
            recommended_pattern=item.get("recommended_pattern"),
            default_capabilities=default_capabilities,
            bindings=bindings,
        )
    return Purposes(purposes=out)


def _parse_default_capability(raw: dict, purpose_idx: int, cap_idx: int) -> Capability:
    """Parse a default-capability entry from a purpose YAML block.

    Schema (minimal — only what an operator needs to spawn a session
    with useful pre-granted caps):

      - kind: READ_FS                 # required, must be a CapabilityKind
        pattern: "/home/me/research/**"  # required
        origin: operator              # optional; defaults to operator
        allows_destructive: false     # optional; defaults to false
        max_amount: 0                 # optional; 0 = unlimited
        rate_limit: null              # optional; cap.uses-per-window
    """
    if not isinstance(raw, dict):
        raise PurposeError(
            f"purposes[{purpose_idx}].default_capabilities[{cap_idx}] must be an object",
        )
    try:
        kind_str = str(raw["kind"])
    except KeyError:
        raise PurposeError(
            f"purposes[{purpose_idx}].default_capabilities[{cap_idx}] missing 'kind'",
        ) from None
    try:
        kind = CapabilityKind(kind_str)
    except ValueError as e:
        raise PurposeError(
            f"purposes[{purpose_idx}].default_capabilities[{cap_idx}] unknown kind {kind_str!r}",
        ) from e
    try:
        pattern = str(raw["pattern"])
    except KeyError:
        raise PurposeError(
            f"purposes[{purpose_idx}].default_capabilities[{cap_idx}] missing 'pattern'",
        ) from None
    # Build the Capability directly; from_dict requires audit_id +
    # expiry which aren't in the operator's YAML by design. Defaults
    # (SESSION expiry, OPERATOR origin, fresh audit_id) match how a
    # manual `capdep grant` builds caps.
    from datetime import UTC
    from datetime import datetime as _dt

    from capabledeputy.policy.capabilities import (
        CapabilityExpiry,
        CapabilityOrigin,
    )

    try:
        origin_str = raw.get("origin") or "user_approved"
        origin = CapabilityOrigin(str(origin_str))
    except ValueError as e:
        raise PurposeError(
            f"purposes[{purpose_idx}].default_capabilities[{cap_idx}] "
            f"unknown origin {raw.get('origin')!r}",
        ) from e
    expires_at = None
    if raw.get("expires_at"):
        try:
            expires_at = _dt.fromisoformat(str(raw["expires_at"]))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
        except ValueError as e:
            raise PurposeError(
                f"purposes[{purpose_idx}].default_capabilities[{cap_idx}] "
                f"unparseable expires_at: {raw.get('expires_at')!r}",
            ) from e
    max_amount = raw.get("max_amount")
    if max_amount is not None:
        max_amount = int(max_amount)
    try:
        return Capability(
            kind=kind,
            pattern=pattern,
            expiry=CapabilityExpiry.SESSION,
            origin=origin,
            allows_destructive=bool(raw.get("allows_destructive", False)),
            max_amount=max_amount,
            expires_at=expires_at,
        )
    except (ValueError, TypeError) as e:
        raise PurposeError(
            f"purposes[{purpose_idx}].default_capabilities[{cap_idx}] malformed: {e}",
        ) from e


def _parse_purpose_binding(
    raw: dict, purpose_idx: int, binding_idx: int
) -> SourceLocationLabelBinding:
    """Parse a per-purpose binding entry.

    Minimal schema (operator-friendly subset of the full binding model):
      - scope_pattern_canonical: glob   (required)
      - category: str                   (required)
      - default_tier: tier-name         (required)
      - assignment_provenance: str      (optional; defaults to purpose-id)
    """
    if not isinstance(raw, dict):
        raise PurposeError(
            f"purposes[{purpose_idx}].bindings[{binding_idx}] must be an object",
        )
    for key in ("scope_pattern_canonical", "category", "default_tier"):
        if key not in raw:
            raise PurposeError(
                f"purposes[{purpose_idx}].bindings[{binding_idx}] missing {key!r}",
            )
    try:
        tier = Tier(str(raw["default_tier"]))
    except ValueError as e:
        raise PurposeError(
            f"purposes[{purpose_idx}].bindings[{binding_idx}] "
            f"unknown default_tier {raw['default_tier']!r}",
        ) from e
    # `name` is required by the SourceLocationLabelBinding model;
    # synthesize a stable one from purpose + position if the operator
    # didn't supply one.
    default_name = f"purpose:{purpose_idx}:binding:{binding_idx}"
    return SourceLocationLabelBinding(
        name=str(raw.get("name", default_name)),
        scope_pattern_canonical=str(raw["scope_pattern_canonical"]),
        category=str(raw["category"]),
        default_tier=tier,
        assignment_provenance=str(raw.get("assignment_provenance", "purpose-declared")),
    )


# --- T056 helper: capability admissibility check --------------------


def categories_of_capability(
    cap_categories: frozenset[str],
    purposes: Purposes,
    purpose_handle: str,
) -> frozenset[str]:
    """Return the subset of `cap_categories` that are inadmissible
    under the given purpose. Empty result ⇒ the capability is
    admissible. Used by SessionGraph.new and .delegate to refuse
    grants that would introduce a forbidden category (FR-009).

    The caller supplies the categories that a capability's read
    scope spans — this module deliberately does not infer them
    from a Capability instance; the data-flow is operator-supplied
    label data, not anything the LLM can influence.
    """
    return frozenset(c for c in cap_categories if not purposes.admits(purpose_handle, c))
