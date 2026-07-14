"""Dynamic filesystem labeling (Issue #5, FR-024/FR-025).

Today a raw `fs.read` of `~/Documents/Financial/budget.pdf` and of
`/etc/hosts` come back with the *same* labels — Axis A (data category) is
dead for filesystem data, so egress checks have nothing to fire on. This
module attaches source-category labels to filesystem reads at read time,
so the IFC model covers local files: once `~/Documents/Financial/**` is
labeled `financial`, that label propagates through every later decision
and the bait-and-pivot exfil pattern is structurally blocked.

Three declarative tiers, all **raise-only** (labels can escalate, never
drop — composition is `most_restrictive_inherit`, which is monotone):

  1. path-prefix   — fast, matched on the (expanduser'd) path
  2. filename-glob — `*.key`, `id_rsa*`, `password*.txt`, ...
  3. content-regex — opt-in; only applied when the caller passes content
     (e.g. "BEGIN OPENSSH PRIVATE KEY")

Config lives in `configs/fs_label_rules.yaml`; an empty/absent file means
"no fs labeling" (the cold-start default stays permissive on category,
while provenance still marks fs reads EXTERNAL_UNTRUSTED downstream).

A Starlark escape hatch for complex predicates (the `starlark:` match key
in the issue sketch) is deliberately deferred — see the spec doc — so the
YAML stays declarative for the 80% case first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from capabledeputy.policy.labels import (
    CategoryTag,
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
    _category_tier,
    most_restrictive_inherit,
)


class FsLabelRuleError(RuntimeError):
    """A fs_label_rules.yaml entry is malformed. Fail-closed (Principle
    VI): a misconfigured labeler refuses daemon start rather than
    silently under-labeling sensitive files."""


# Provenance-only label strings reuse the canonical mapping; category
# strings of the form "confidential.<category>" resolve their tier from
# the labels.yaml catalog (#50), defaulting to REGULATED for an unknown
# category so a typo never *under*-classifies.
_PROVENANCE_LABELS: dict[str, ProvenanceLevel] = {
    "untrusted.external": ProvenanceLevel.EXTERNAL_UNTRUSTED,
    "untrusted.user_input": ProvenanceLevel.EXTERNAL_UNTRUSTED,
    "trusted.user_direct": ProvenanceLevel.PRINCIPAL_DIRECT,
}


def label_string_to_state(label: str) -> LabelState:
    """Public wrapper: resolve a `confidential.<category>` / provenance label
    string to a LabelState (tier resolved from configs/labels.yaml). Used by the
    ingest path to apply an explicit operator-declared category."""
    return _label_string_to_state(label)


def _label_string_to_state(label: str) -> LabelState:
    if label in _PROVENANCE_LABELS:
        return LabelState(b=frozenset({ProvenanceTag(_PROVENANCE_LABELS[label])}))
    if label.startswith("confidential."):
        category = label.split(".", 1)[1]
        if not category:
            raise FsLabelRuleError(f"empty category in label {label!r}")
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
    raise FsLabelRuleError(
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
    raise FsLabelRuleError(f"expected a string or list, got {type(value).__name__}")


@dataclass(frozen=True)
class FsLabelRule:
    """One declarative labeling rule. A rule matches when ANY of its
    configured match facets matches; the union of all matching rules'
    labels is attached (raise-only)."""

    labels: LabelState
    path_prefixes: tuple[str, ...] = ()
    filename_globs: tuple[str, ...] = ()
    content_regexes: tuple[re.Pattern[str], ...] = ()

    @property
    def needs_content(self) -> bool:
        return bool(self.content_regexes)

    def matches(self, *, expanded_path: str, name: str, content: str | None) -> bool:
        for prefix in self.path_prefixes:
            if expanded_path.startswith(prefix):
                return True
        for glob in self.filename_globs:
            if fnmatch(name, glob):
                return True
        if content is not None:
            for rx in self.content_regexes:
                if rx.search(content):
                    return True
        return False


@dataclass(frozen=True)
class FsLabeler:
    """Applies the loaded rules to a path (+ optional content) and returns
    the LabelState to attach. Empty rule set ⇒ empty state (no-op)."""

    rules: tuple[FsLabelRule, ...] = ()

    @property
    def any_content_rules(self) -> bool:
        return any(r.needs_content for r in self.rules)

    def labels_for(self, path: str, *, content: str | None = None) -> LabelState:
        if not self.rules:
            return LabelState()
        expanded = str(Path(path).expanduser())
        name = Path(path).name
        state = LabelState()
        for rule in self.rules:
            if rule.matches(expanded_path=expanded, name=name, content=content):
                state = most_restrictive_inherit(state, rule.labels)
        return state


def _parse_rule(index: int, raw: Any) -> FsLabelRule:
    if not isinstance(raw, dict):
        raise FsLabelRuleError(f"fs_label_rules[{index}] must be a mapping")
    match = raw.get("match")
    if not isinstance(match, dict):
        raise FsLabelRuleError(f"fs_label_rules[{index}].match must be a mapping")
    labels_raw = _as_list(raw.get("labels"))
    if not labels_raw:
        raise FsLabelRuleError(f"fs_label_rules[{index}] must declare at least one label")
    labels = LabelState()
    for label in labels_raw:
        labels = most_restrictive_inherit(labels, _label_string_to_state(label))

    prefixes = tuple(str(Path(p).expanduser()) for p in _as_list(match.get("path_prefix")))
    globs = tuple(_as_list(match.get("filename_glob")))
    try:
        regexes = tuple(re.compile(r) for r in _as_list(match.get("content_regex")))
    except re.error as e:
        raise FsLabelRuleError(f"fs_label_rules[{index}] bad content_regex: {e}") from e

    if not (prefixes or globs or regexes):
        raise FsLabelRuleError(
            f"fs_label_rules[{index}].match needs one of path_prefix / "
            "filename_glob / content_regex",
        )
    return FsLabelRule(
        labels=labels,
        path_prefixes=prefixes,
        filename_globs=globs,
        content_regexes=regexes,
    )


def parse_fs_label_rules(raw: Any) -> FsLabeler:
    """Build an FsLabeler from already-parsed YAML (a list of rules, or a
    mapping with a top-level `fs_label_rules:` key)."""
    if raw is None:
        return FsLabeler()
    if isinstance(raw, dict):
        raw = raw.get("fs_label_rules", [])
    if not isinstance(raw, list):
        raise FsLabelRuleError("fs_label_rules must be a list")
    return FsLabeler(rules=tuple(_parse_rule(i, r) for i, r in enumerate(raw)))


def load_fs_label_rules(path: Path) -> FsLabeler:
    """Load the labeler from `configs/fs_label_rules.yaml`. Absent file ⇒
    empty labeler (fs labeling simply off). Unparseable ⇒ fail-closed."""
    if not path.is_file():
        return FsLabeler()
    import yaml

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise FsLabelRuleError(f"fs_label_rules unparseable: {path} — {e}") from e
    return parse_fs_label_rules(raw)
