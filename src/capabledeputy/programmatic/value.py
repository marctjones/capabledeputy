"""LabeledValue: wraps any Python value with a frozenset of Labels.

Every value that flows through the interpreter is a LabeledValue. Binary
operators, function calls, attribute access, and indexing all union the
labels of their operands so that an output's label set is at least the
union of every input that contributed to it (DESIGN.md §3 — provenance
tracking).

Helper `unwrap` strips wrappers recursively (descending into containers)
to produce the plain Python value the underlying tool / builtin needs.
Helper `labels_of` collects the union of labels from a (possibly nested)
value without unwrapping.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from capabledeputy.policy.labels import Label


@dataclass(frozen=True)
class LabeledValue:
    raw: Any
    labels: frozenset[Label] = field(default_factory=frozenset)

    def with_labels(self, extra: frozenset[Label]) -> LabeledValue:
        if not extra or extra <= self.labels:
            return self
        return LabeledValue(raw=self.raw, labels=self.labels | extra)


def lv(raw: Any, labels: frozenset[Label] = frozenset()) -> LabeledValue:
    if isinstance(raw, LabeledValue):
        return raw.with_labels(labels)
    return LabeledValue(raw=raw, labels=labels)


def unwrap(v: Any) -> Any:
    if isinstance(v, LabeledValue):
        return unwrap(v.raw)
    if isinstance(v, list):
        return [unwrap(x) for x in v]
    if isinstance(v, tuple):
        return tuple(unwrap(x) for x in v)
    if isinstance(v, dict):
        return {unwrap(k): unwrap(x) for k, x in v.items()}
    if isinstance(v, set):
        return {unwrap(x) for x in v}
    return v


def labels_of(v: Any) -> frozenset[Label]:
    if isinstance(v, LabeledValue):
        return v.labels | labels_of(v.raw)
    if isinstance(v, (list, tuple, set)):
        out: frozenset[Label] = frozenset()
        for x in v:
            out = out | labels_of(x)
        return out
    if isinstance(v, dict):
        out = frozenset()
        for k, val in v.items():
            out = out | labels_of(k) | labels_of(val)
        return out
    return frozenset()


def union_labels(*values: Any) -> frozenset[Label]:
    out: frozenset[Label] = frozenset()
    for value in values:
        out = out | labels_of(value)
    return out
