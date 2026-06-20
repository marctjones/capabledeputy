"""Source/Location Label Bindings (003 US6 / FR-043 / FR-048).

A SourceLocationLabelBinding attaches a label set + tier + optional
reversibility/mutability/write_discipline to a *canonical scope*
(e.g., `file:///abs/path/**`, `https://host/...`, `mcp:server/id`).
The resolver consults bindings on every read/ingest AND every
write/egress, so a flow from `file:///HR/employees/*.csv` to
`https://teams.sharepoint.com/...` is gated by named operator
declarations rather than per-flow ad-hoc judgement.

Fail-closed rules (FR-023):
  - An input URI that doesn't canonicalize ⇒ refused; we never
    "best-effort" a partial canonicalization.
  - An input URI that canonicalizes but matches no binding ⇒
    refused (operator must declare; absence is not permission).
  - Overlapping bindings ⇒ most-restrictive composition per
    dimension (FR-024).

The "canonical destination id" surface (FR-048) is the same resolver
queried with the write target — produces a stable id the auditor
can correlate, never "raw path the model typed."
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import yaml

from capabledeputy.policy.reversibility import MutabilityLabel, ReversibilityLabel
from capabledeputy.policy.tiers import Tier, max_of


class BindingError(RuntimeError):
    """source_bindings.yaml is malformed or a URI cannot be
    canonicalized — fail-closed per Principle VI."""


class WriteDiscipline(StrEnum):
    """Required write discipline on the bound destination.
    `version-preserving` means writes MUST go through the versioned-
    write port (FR-044). `in-place` is the default for unflagged
    destinations."""

    VERSION_PRESERVING = "version-preserving"
    IN_PLACE = "in-place"


# --- canonicalization ------------------------------------------------


_SUPPORTED_SCHEMES = frozenset(
    {
        "file",
        "unc",
        "http",
        "https",
        "mcp",
        "gmail",
        "gdoc",
        "gdrive",
        "gcal",
        "gchat",
        "people",
        "applemail",
        "pages",
        "numbers",
        "keynote",
        "macos",
        "imap",
    },
)


def canonicalize(uri: str) -> str:
    """Canonicalize a URI to its scheme-normalized form.

    Rules:
      - scheme is lowercased,
      - hostname is lowercased (URLs/UNC only),
      - trailing slashes on paths are not stripped (a directory and
        its index page are distinguishable),
      - fragments are dropped (locator state, not identity),
      - query strings are preserved.

    Per-scheme canonicalizers can be added incrementally; today the
    standard library's urlsplit covers file://, http(s)://, mcp:,
    and unc:// (the latter via path normalization).
    """
    if uri.startswith("/"):
        return f"file://{uri}"
    if not uri or ":" not in uri:
        raise BindingError(f"cannot canonicalize {uri!r}: not a URI")
    try:
        parts = urlsplit(uri)
    except ValueError as e:
        raise BindingError(f"cannot canonicalize {uri!r}: {e}") from e
    scheme = parts.scheme.lower()
    if scheme not in _SUPPORTED_SCHEMES:
        raise BindingError(
            f"cannot canonicalize {uri!r}: unsupported scheme {scheme!r} "
            f"(supported: {sorted(_SUPPORTED_SCHEMES)})",
        )
    netloc = parts.netloc.lower()
    return urlunsplit((scheme, netloc, parts.path, parts.query, ""))


# --- binding model ---------------------------------------------------


@dataclass(frozen=True)
class SourceLocationLabelBinding:
    """One operator-authored binding. `scope_pattern_canonical` is a
    glob over the canonicalized URI space."""

    name: str
    scope_pattern_canonical: str
    category: str
    default_tier: Tier
    reversibility: ReversibilityLabel | None = None
    mutability: MutabilityLabel | None = None
    write_discipline: WriteDiscipline = WriteDiscipline.IN_PLACE
    risk_ids: tuple[str, ...] = field(default_factory=tuple)
    assignment_provenance: str = "operator-declared"

    def matches(self, canonical_uri: str) -> bool:
        return fnmatch.fnmatchcase(canonical_uri, self.scope_pattern_canonical)

    def specificity(self) -> int:
        """Higher = more specific.

        Specificity is measured by literal characters across the glob,
        not just the prefix before the first wildcard. That matters for
        operator-friendly home-directory patterns like
        ``file:///Users/*/Documents/GitHub/**``: the user segment is a
        wildcard, but the later ``Documents/GitHub`` literal should still
        beat a broader ``file:///Users/*/Documents/**`` binding.
        """
        score = 0
        in_char_class = False
        for ch in self.scope_pattern_canonical:
            if in_char_class:
                if ch == "]":
                    in_char_class = False
                continue
            if ch == "[":
                in_char_class = True
                continue
            if ch in "*?":
                continue
            score += 1
        return score


@dataclass(frozen=True)
class BindingResolution:
    """Result of resolving a canonical URI through the binding set.

    Multiple bindings may match (overlapping subtrees); category and
    tier come from the *most-specific* binding; reversibility,
    mutability, and write_discipline compose most-restrictive across
    all matched bindings (FR-043). `canonical_destination_id` is the
    canonical URI the resolver locked onto; auditors should record
    THAT, not the model's raw input (FR-048)."""

    canonical_destination_id: str
    matched_bindings: tuple[SourceLocationLabelBinding, ...]
    category: str
    tier: Tier
    reversibility: ReversibilityLabel | None
    mutability: MutabilityLabel | None
    write_discipline: WriteDiscipline
    risk_ids: tuple[str, ...]
    assignment_provenance: str


@dataclass(frozen=True)
class BindingSet:
    """Loaded operator binding catalogue."""

    bindings: tuple[SourceLocationLabelBinding, ...]

    def resolve(self, uri: str) -> BindingResolution:
        """Resolve `uri` to a BindingResolution. Raises BindingError
        if the URI cannot be canonicalized OR no binding matches —
        per FR-023, absence of a binding is fail-closed.

        Composition rules:
          - Most-specific binding wins for category + tier (one
            wins; if there's a true tie on specificity, the
            stricter tier wins via Tier.max_of).
          - Reversibility / mutability / write_discipline compose
            most-restrictive across ALL matched bindings.
          - risk_ids set-union across all matched bindings.
        """
        canon = canonicalize(uri)
        matched = tuple(b for b in self.bindings if b.matches(canon))
        if not matched:
            raise BindingError(
                f"no binding matches {canon!r}; unbound destination is fail-closed per FR-023",
            )
        most_specific = max(matched, key=lambda b: b.specificity())
        category = most_specific.category
        # Tier: take the most-restrictive across matched bindings of
        # the same category (different category bindings are kept
        # separate by category field; this is a per-category set).
        same_cat = [b for b in matched if b.category == category]
        tier = same_cat[0].default_tier
        for b in same_cat[1:]:
            tier = max_of(tier, b.default_tier)

        rev = _compose_reversibility_opt(*(b.reversibility for b in matched))
        mut = _compose_mutability_opt(*(b.mutability for b in matched))

        # Write discipline: version-preserving is strictly more
        # restrictive than in-place; pick the strictest if any
        # matched binding demands version-preserving.
        wd = (
            WriteDiscipline.VERSION_PRESERVING
            if any(b.write_discipline is WriteDiscipline.VERSION_PRESERVING for b in matched)
            else WriteDiscipline.IN_PLACE
        )

        risks: set[str] = set()
        for b in matched:
            risks.update(b.risk_ids)
        # assignment_provenance from the most-specific binding;
        # operator declarations are authoritative (FR-022).
        return BindingResolution(
            canonical_destination_id=canon,
            matched_bindings=matched,
            category=category,
            tier=tier,
            reversibility=rev,
            mutability=mut,
            write_discipline=wd,
            risk_ids=tuple(sorted(risks)),
            assignment_provenance=most_specific.assignment_provenance,
        )


def _compose_reversibility_opt(
    *labels: ReversibilityLabel | None,
) -> ReversibilityLabel | None:
    present = tuple(lbl for lbl in labels if lbl is not None)
    if not present:
        return None
    from capabledeputy.policy.reversibility import compose_reversibility

    return compose_reversibility(*present)


def _compose_mutability_opt(
    *labels: MutabilityLabel | None,
) -> MutabilityLabel | None:
    present = tuple(lbl for lbl in labels if lbl is not None)
    if not present:
        return None
    from capabledeputy.policy.reversibility import compose_mutability

    return compose_mutability(*present)


# --- YAML loader ----------------------------------------------------


def load(path: Path) -> BindingSet:
    """Load configs/source_bindings.yaml. Fail-closed on missing or
    unparseable file. Empty `bindings:` permitted — yields a set
    that refuses every URI (FR-023)."""
    if not path.is_file():
        raise BindingError(f"source_bindings config missing: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise BindingError(f"unparseable: {path} — {e}") from e
    if data is None:
        return BindingSet(bindings=())
    raw = data.get("bindings") or []
    if not isinstance(raw, list):
        raise BindingError(f"'bindings' must be a list: {path}")
    parsed: list[SourceLocationLabelBinding] = []
    seen_names: set[str] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise BindingError(f"bindings[{i}] is not an object")
        try:
            name = str(item["name"])
            category = str(item["category"])
            default_tier = Tier(str(item["default_tier"]))
        except (KeyError, ValueError) as e:
            raise BindingError(f"bindings[{i}]: {e}") from e
        if name in seen_names:
            raise BindingError(f"bindings[{i}] duplicate name: {name!r}")
        seen_names.add(name)
        rev_raw = item.get("reversibility")
        mut_raw = item.get("mutability")
        rev = ReversibilityLabel.from_dict(rev_raw) if isinstance(rev_raw, dict) else None
        mut = MutabilityLabel.from_dict(mut_raw) if isinstance(mut_raw, dict) else None
        wd_raw = item.get("write_discipline", "in-place")
        try:
            wd = WriteDiscipline(str(wd_raw))
        except ValueError as e:
            raise BindingError(f"bindings[{i}].write_discipline: {e}") from e
        parsed.append(
            SourceLocationLabelBinding(
                name=name,
                scope_pattern_canonical=str(item["scope_pattern_canonical"]),
                category=category,
                default_tier=default_tier,
                reversibility=rev,
                mutability=mut,
                write_discipline=wd,
                risk_ids=tuple(str(r) for r in (item.get("risk_ids") or [])),
                assignment_provenance=str(
                    item.get("assignment_provenance", "operator-declared"),
                ),
            ),
        )
    return BindingSet(bindings=tuple(parsed))
