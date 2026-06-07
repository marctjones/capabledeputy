"""Loader: daemon-config `decision_inspectors:` block → registered
inspectors (Issue #46).

This is the wire that turns the *dormant* decision-refinement layer ON.
The `DecisionInspector` chokepoint (`tools/client.py:_apply_decision_inspectors`)
and the sandboxed Starlark `PolicyScriptHost` were built and tested, but
nothing populated `PolicyContext.decision_inspectors`. This module reads
an operator-authored `decision_inspectors:` list from the daemon config
and produces the inspector tuple the chokepoint composes after every
standard policy decision.

Two entry kinds:

  - **builtin** — a reviewed reference inspector shipped in-repo
    (`self_egress_relaxer`, `after_hours_purchase_tightener`),
    parameterized by the config entry.
  - **script** / **source** — an operator-authored policy compiled
    through `get_script_host(runtime)` (default `starlark`, the real
    language-level sandbox) and wrapped in a `ScriptDecisionInspector`
    adapter that bridges the script's `relax/tighten/abstain` result to
    a `DecisionRelax`/`DecisionTighten`.

Fail-closed (Constitution VI): a malformed entry or a script that fails
to compile raises `DecisionInspectorConfigError` at load time, so the
daemon refuses to start rather than run with a partially-loaded
refinement layer. A script that *errors at evaluation* is a different
case — the chokepoint already catches per-inspector exceptions, audits
them, and treats them as abstain, so one buggy script never crashes a
decision.

Guardrail (the layer refines, never overrides a floor): a script can
only *relax within the envelope cell* and can never cross a structural
DENY floor. That bound is enforced downstream — `compose_inspector_outcomes`
discards non-monotone moves and the engine's bounded-relax (FR-026)
clamps a relax to the envelope — not here; this module only loads.
"""

from __future__ import annotations

from inspect import isawaitable
from pathlib import Path
from typing import Any

from capabledeputy.policy.rules import Decision
from capabledeputy.substrate.decision_inspector_port import (
    DecisionRelax,
    DecisionTighten,
)
from capabledeputy.substrate.decision_inspectors_builtin import (
    AfterHoursPurchaseTightener,
    SelfEgressRelaxer,
)
from capabledeputy.substrate.policy_script_host import (
    PolicyScriptHostUnavailableError,
    get_script_host,
)


class DecisionInspectorConfigError(RuntimeError):
    """A `decision_inspectors:` entry is malformed or a script fails to
    compile. Fail-closed (Principle VI): the daemon must refuse to start
    rather than run a partially-configured refinement layer."""


class DecisionInspectorScriptError(RuntimeError):
    """A compiled script errored or returned an unusable result at
    EVALUATION time. Raised from the adapter so the chokepoint's
    per-inspector try/except audits it and treats it as abstain — one
    buggy script never crashes a decision."""


# --- hermetic input projection ---------------------------------------
#
# Scripts see ONLY these dicts — no host objects, no session history
# (yet — #48 threads a read-only history summary into `session`). Keep
# the surface small and stable so a script written today keeps working.


def _action_to_dict(action: Any) -> dict[str, Any]:
    kind = getattr(action.kind, "value", str(action.kind))
    return {
        "kind": kind,
        "target": (getattr(action, "target", "") or ""),
        "amount": getattr(action, "amount", None),
    }


def _history_summary(session: Any) -> dict[str, Any]:
    """A bounded, read-only history summary (#48) so scripts can express
    frequency / aggregation logic ("> N sends this session"). Cumulative,
    clock-free: `counts_by_kind` sums gated uses per capability kind from
    `cap_uses` (keyed by capability audit_id → kind via the capability
    set). `used_kinds` is the set of kinds exercised so far.
    """
    cap_uses = getattr(session, "cap_uses", None) or {}
    caps = getattr(session, "capability_set", None) or frozenset()
    aid_to_kind = {
        str(c.audit_id): getattr(c.kind, "value", str(c.kind))
        for c in caps
        if hasattr(c, "audit_id")
    }
    counts_by_kind: dict[str, int] = {}
    for aid, stamps in cap_uses.items():
        kind = aid_to_kind.get(str(aid))
        if kind is None:
            continue
        counts_by_kind[kind] = counts_by_kind.get(kind, 0) + len(stamps)
    used_kinds = sorted(
        {getattr(k, "value", str(k)) for k in getattr(session, "used_kinds", None) or ()},
    )
    return {
        "counts_by_kind": counts_by_kind,
        "used_kinds": used_kinds,
        "total_uses": sum(counts_by_kind.values()),
    }


def _session_to_dict(session: Any) -> dict[str, Any]:
    label_state = getattr(session, "label_state", None)
    categories: list[str] = []
    provenance: list[str] = []
    tiers: list[str] = []
    if label_state is not None:
        categories = sorted({t.category for t in label_state.a})
        tiers = sorted({getattr(t.tier, "value", str(t.tier)) for t in label_state.a})
        provenance = sorted(
            {getattr(t.level, "value", str(t.level)) for t in label_state.b},
        )
    return {
        "purpose": (getattr(session, "purpose_handle", "") or ""),
        "categories": categories,
        "tiers": tiers,
        "provenance": provenance,
        "risk_preference": getattr(session, "risk_preference_at_spawn", "cautious"),
        # #48 — bounded read-only history summary for frequency/aggregation.
        "history": _history_summary(session),
    }


def _proposed_to_dict(proposed: Any) -> dict[str, Any]:
    return {
        "decision": getattr(proposed.decision, "value", str(proposed.decision)),
        "rule": (getattr(proposed, "rule", "") or ""),
        "reason": (getattr(proposed, "reason", "") or ""),
    }


def _outcome_to_adjustment(
    outcome: Any,
) -> DecisionRelax | DecisionTighten | None:
    if outcome.kind == "abstain":
        return None
    if outcome.kind == "error":
        raise DecisionInspectorScriptError(outcome.error or "script evaluation error")
    try:
        to = Decision(outcome.to_decision)
    except ValueError as e:
        raise DecisionInspectorScriptError(
            f"script returned unknown decision {outcome.to_decision!r}",
        ) from e
    if outcome.kind == "relax":
        return DecisionRelax(to=to, rule=outcome.rule, rationale=outcome.rationale)
    return DecisionTighten(to=to, rule=outcome.rule, rationale=outcome.rationale)


class ScriptDecisionInspector:
    """Adapts a compiled `PolicyScript` to the `DecisionInspector`
    protocol. `inspect` is async (it awaits the host's `evaluate`, which
    runs the script off the event loop); the chokepoint awaits async
    inspectors. Builtins stay synchronous — both are supported.
    """

    def __init__(self, name: str, host: Any, script: Any) -> None:
        self.name = name
        self._host = host
        self._script = script

    async def inspect(
        self,
        *,
        action: Any,
        session: Any,
        proposed_outcome: Any,
    ) -> DecisionRelax | DecisionTighten | None:
        outcome = await self._host.evaluate(
            self._script,
            action=_action_to_dict(action),
            session=_session_to_dict(session),
            proposed_outcome=_proposed_to_dict(proposed_outcome),
        )
        return _outcome_to_adjustment(outcome)


def _load_builtin(index: int, entry: dict[str, Any]) -> Any:
    name = entry["builtin"]
    if name == "self_egress_relaxer":
        return SelfEgressRelaxer(
            self_addresses=frozenset(entry.get("self_addresses", [])),
            action_kinds=frozenset(entry.get("action_kinds", ["SEND_EMAIL"])),
        )
    if name == "after_hours_purchase_tightener":
        return AfterHoursPurchaseTightener(
            start_hour_utc=int(entry.get("start_hour_utc", 22)),
            end_hour_utc=int(entry.get("end_hour_utc", 6)),
            action_kinds=frozenset(entry.get("action_kinds", ["QUEUE_PURCHASE"])),
        )
    raise DecisionInspectorConfigError(
        f"decision_inspectors[{index}]: unknown builtin {name!r} "
        "(known: self_egress_relaxer, after_hours_purchase_tightener)",
    )


def _load_script(
    index: int,
    entry: dict[str, Any],
    base_dir: Path | None,
) -> ScriptDecisionInspector:
    runtime = str(entry.get("runtime", "starlark"))
    if "source" in entry:
        source = str(entry["source"])
        name = str(entry.get("name", f"inline-{index}"))
    else:
        rel = Path(str(entry["script"]))
        path = rel if rel.is_absolute() or base_dir is None else base_dir / rel
        try:
            source = path.read_text(encoding="utf-8")
        except OSError as e:
            raise DecisionInspectorConfigError(
                f"decision_inspectors[{index}]: cannot read script {path}: {e}",
            ) from e
        name = str(entry.get("name", path.stem))
    try:
        host = get_script_host(runtime)
        script = host.compile(name, source)
    except (ValueError, PolicyScriptHostUnavailableError) as e:
        raise DecisionInspectorConfigError(
            f"decision_inspectors[{index}]: script {name!r} ({runtime}) "
            f"failed to compile: {e}",
        ) from e
    return ScriptDecisionInspector(name, host, script)


def load_decision_inspectors(
    config: dict[str, Any] | None,
    *,
    base_dir: Path | None = None,
) -> tuple[Any, ...]:
    """Build the DecisionInspector tuple from a daemon-config mapping.

    `config` is the parsed daemon config (or None / missing block → no
    inspectors). `base_dir` resolves relative `script:` paths (typically
    the daemon config's directory). Fail-closed on any malformed entry.
    """
    if not config:
        return ()
    entries = config.get("decision_inspectors")
    if entries is None:
        return ()
    if not isinstance(entries, list):
        raise DecisionInspectorConfigError("decision_inspectors must be a list")

    inspectors: list[Any] = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise DecisionInspectorConfigError(
                f"decision_inspectors[{i}] must be a mapping",
            )
        if "builtin" in entry:
            inspectors.append(_load_builtin(i, entry))
        elif "script" in entry or "source" in entry:
            inspectors.append(_load_script(i, entry, base_dir))
        else:
            raise DecisionInspectorConfigError(
                f"decision_inspectors[{i}] must declare one of "
                "'builtin', 'script', or 'source'",
            )
    return tuple(inspectors)


# Exposed so the chokepoint can detect & await async inspectors without
# importing this module's adapter directly.
__all__ = [
    "DecisionInspectorConfigError",
    "DecisionInspectorScriptError",
    "ScriptDecisionInspector",
    "isawaitable",
    "load_decision_inspectors",
]
