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
        out[pid] = Purpose(
            purpose_id=pid,
            label=str(item.get("label", "")),
            admissible_categories=frozenset(str(c) for c in admissible_raw),
            inadmissible_categories=frozenset(str(c) for c in inadmissible_raw),
            recommended_pattern=item.get("recommended_pattern"),
        )
    return Purposes(purposes=out)


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
