"""LabeledValue: wraps any Python value with a four-axis LabelState.

Every value that flows through the interpreter is a LabeledValue. Binary
operators, function calls, attribute access, and indexing all compose the
label_state of their operands so that an output's tags are at least the
union of every input that contributed to it (DESIGN.md §3 — provenance
tracking).

Helper `unwrap` strips wrappers recursively (descending into containers)
to produce the plain Python value the underlying tool / builtin needs.
Helper `tags_of` collects the composition of tags from a (possibly nested)
value without unwrapping.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from capabledeputy.policy.labels import LabelState, most_restrictive_inherit


@dataclass(frozen=True)
class LabeledValue:
    raw: Any
    label_state: LabelState = field(default_factory=LabelState)

    def with_tags(self, extra: LabelState) -> LabeledValue:
        composed = most_restrictive_inherit(self.label_state, extra)
        if composed == self.label_state:
            return self
        return LabeledValue(raw=self.raw, label_state=composed)


def lv(raw: Any, label_state: LabelState | None = None) -> LabeledValue:
    if label_state is None:
        label_state = LabelState()
    if isinstance(raw, LabeledValue):
        return raw.with_tags(label_state)
    return LabeledValue(raw=raw, label_state=label_state)


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


def tags_of(v: Any) -> LabelState:
    if isinstance(v, LabelState):
        return v
    if isinstance(v, LabeledValue):
        return most_restrictive_inherit(v.label_state, tags_of(v.raw))
    if isinstance(v, (list, tuple, set)):
        states: list[LabelState] = []
        for x in v:
            states.append(tags_of(x))
        return most_restrictive_inherit(*states) if states else LabelState()
    if isinstance(v, dict):
        states: list[LabelState] = []
        for k, val in v.items():
            states.append(tags_of(k))
            states.append(tags_of(val))
        return most_restrictive_inherit(*states) if states else LabelState()
    return LabelState()


def union_tags(*values: Any) -> LabelState:
    states: list[LabelState] = []
    for value in values:
        states.append(tags_of(value))
    return most_restrictive_inherit(*states) if states else LabelState()


# Backward-compat aliases for evaluator.py (which is not in the migration scope)
labels_of = tags_of
union_labels = union_tags
