"""Expectation Binding registry (003 US2 / FR-029).

Deterministic registry: a tuple (initiator, effect_kind, time_window,
param_constraints) is *expected* iff it matches a registered binding.
NO heuristic anomaly inference allowed — this is a pure registry
match, evaluated in the decision path (Principle I: no LLM in the
decision).

Loaded from configs/expectations.yaml. Empty registry is valid (no
bindings declared yet); match() returns False (anomalous) in that case.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any

import yaml


class ExpectationError(RuntimeError):
    """The expectations config is malformed or unparseable. Fail-closed
    per Principle VI."""


@dataclass(frozen=True)
class ExpectationBinding:
    """A single expectation binding.

    `time_window` is a (start_hour, end_hour) tuple in UTC, or None for
    "any time". `param_constraints` is a dict of param-name -> exact-
    value-or-allowed-set; missing or empty means "any params". Richer
    constraints (regex, ranges) are a deliberate non-goal until a user
    story needs them.
    """

    binding_id: str
    initiator: str  # exact match against a runtime initiator id
    effect_kind: str
    time_window: tuple[int, int] | None = None
    param_constraints: dict[str, Any] = field(default_factory=dict)
    risk_ids: tuple[str, ...] = field(default_factory=tuple)

    def matches(
        self,
        *,
        initiator: str,
        effect_kind: str,
        params: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> bool:
        """True iff this binding matches the input tuple."""
        if self.initiator != initiator:
            return False
        if self.effect_kind != effect_kind:
            return False
        if self.time_window is not None:
            t = (now or datetime.now(UTC)).time()
            start_h, end_h = self.time_window
            start = time(hour=start_h)
            end = time(hour=end_h)
            if start <= end:
                if not (start <= t <= end):
                    return False
            else:
                # window wraps midnight (e.g., 22..6).
                if not (t >= start or t <= end):
                    return False
        if self.param_constraints:
            actual = params or {}
            for key, allowed in self.param_constraints.items():
                if key not in actual:
                    return False
                if isinstance(allowed, list | set | tuple):
                    if actual[key] not in allowed:
                        return False
                else:
                    if actual[key] != allowed:
                        return False
        return True


@dataclass(frozen=True)
class ExpectationBindings:
    bindings: tuple[ExpectationBinding, ...]

    def is_expected(
        self,
        *,
        initiator: str,
        effect_kind: str,
        params: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> bool:
        """True iff at least one binding matches. Fail-closed semantics
        live in the caller — `is_expected == False` is the *anomalous*
        signal that decision rules consume; rules then decide whether to
        suggest, require approval, or deny."""
        return any(
            b.matches(initiator=initiator, effect_kind=effect_kind, params=params, now=now)
            for b in self.bindings
        )


def load(path: Path) -> ExpectationBindings:
    if not path.is_file():
        raise ExpectationError(f"expectations config missing: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ExpectationError(f"unparseable: {path} — {e}") from e
    if data is None:
        return ExpectationBindings(bindings=())
    raw = data.get("bindings") or []
    if not isinstance(raw, list):
        raise ExpectationError(f"'bindings' must be a list: {path}")
    parsed: list[ExpectationBinding] = []
    seen_ids: set[str] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ExpectationError(f"bindings[{i}] is not an object")
        try:
            bid = str(item["binding_id"])
            initiator = str(item["initiator"])
            effect_kind = str(item["effect_kind"])
        except KeyError as e:
            raise ExpectationError(
                f"bindings[{i}] missing required: {e.args[0]!r}",
            ) from e
        if bid in seen_ids:
            raise ExpectationError(f"bindings[{i}] duplicate binding_id: {bid!r}")
        seen_ids.add(bid)
        tw_raw = item.get("time_window")
        tw: tuple[int, int] | None = None
        if tw_raw is not None:
            if not (isinstance(tw_raw, list | tuple) and len(tw_raw) == 2):
                raise ExpectationError(
                    f"bindings[{i}].time_window must be [start_hour, end_hour]",
                )
            tw = (int(tw_raw[0]), int(tw_raw[1]))
        params = item.get("param_constraints") or {}
        if not isinstance(params, dict):
            raise ExpectationError(f"bindings[{i}].param_constraints must be a dict")
        parsed.append(
            ExpectationBinding(
                binding_id=bid,
                initiator=initiator,
                effect_kind=effect_kind,
                time_window=tw,
                param_constraints=dict(params),
                risk_ids=tuple(str(r) for r in (item.get("risk_ids") or [])),
            ),
        )
    return ExpectationBindings(bindings=tuple(parsed))
