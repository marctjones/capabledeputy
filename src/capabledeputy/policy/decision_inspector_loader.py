"""Loader: daemon-config `decision_inspectors:` block → registered
inspectors (Issue #46).

This is the wire that turns the *dormant* decision-refinement layer ON.
The `DecisionInspector` chokepoint (`tools/policy_hooks.py`)
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

import os
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


_FAILURE_MODES = frozenset({"abstain", "require_approval", "deny"})


def _failure_mode(entry: dict[str, Any]) -> str:
    mode = str(entry.get("failure_mode", "abstain")).strip().lower()
    if mode not in _FAILURE_MODES:
        raise DecisionInspectorConfigError(
            "decision inspector failure_mode must be one of "
            f"{sorted(_FAILURE_MODES)}, got {mode!r}",
        )
    return mode


def _unsafe_python_reference_allowed() -> bool:
    raw = os.environ.get("CAPDEP_ALLOW_UNSANDBOXED_POLICY_SCRIPTS", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# --- hermetic input projection ---------------------------------------
#
# Scripts see ONLY these dicts — no host objects, no session history
# (yet — #48 threads a read-only history summary into `session`). Keep
# the surface small and stable so a script written today keeps working.


def _action_to_dict(action: Any) -> dict[str, Any]:
    kind = getattr(action.kind, "value", str(action.kind))
    effect_class = getattr(action, "effect_class", None)
    return {
        "kind": kind,
        "target": (getattr(action, "target", "") or ""),
        "amount": getattr(action, "amount", None),
        "tool": getattr(action, "tool_name", None),
        "effect_class": effect_class,
        "external_tool": _external_tool_to_dict(action),
        "flow": _flow_to_dict(kind=kind, effect_class=effect_class),
        "reversibility": _reversibility_to_dict(
            getattr(action, "effective_reversibility", None),
        ),
        # #47 — relationship-group membership of the target (resolved at
        # the chokepoint), so scripts can do relationship-aware relaxes.
        "relationship_groups": sorted(getattr(action, "relationship_group_ids", ()) or ()),
    }


def _external_tool_to_dict(action: Any) -> dict[str, Any]:
    tool_name = getattr(action, "tool_name", None)
    upstream_server = getattr(action, "upstream_server", None)
    return {
        "tool_name": tool_name,
        "upstream_server": upstream_server,
        "resource_uri": getattr(action, "resource_uri", None),
        "prompt_name": getattr(action, "prompt_name", None),
        "is_upstream_mcp": bool(upstream_server),
    }


def _flow_to_dict(*, kind: str, effect_class: Any) -> dict[str, Any]:
    effect = str(effect_class or "")
    effect_lower = effect.lower()
    return {
        "capability_kind": kind,
        "effect_class": effect or None,
        "is_egress": any(token in effect_lower for token in ("egress", "send", "communicate")),
        "is_control_plane": "control" in effect_lower or "setup" in effect_lower,
        "is_local_write": "write" in effect_lower or "modify" in effect_lower,
        "is_destructive": "delete" in effect_lower or "destroy" in effect_lower,
    }


def _reversibility_to_dict(value: Any) -> dict[str, str]:
    if value is None:
        return {"degree": "irreversible", "agent": "external"}
    if isinstance(value, dict):
        return {
            "degree": str(value.get("degree", "irreversible")),
            "agent": str(value.get("agent", "external")),
        }
    degree = getattr(value, "degree", "irreversible")
    agent = getattr(value, "agent", "external")
    return {
        "degree": str(getattr(degree, "value", degree)),
        "agent": str(getattr(agent, "value", agent)),
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
    origin = getattr(session, "origin", None)
    to_dict = getattr(origin, "to_dict", None)
    if callable(to_dict):
        origin_dict = to_dict()
    elif isinstance(origin, dict):
        origin_dict = dict(origin)
    else:
        origin_dict = {"kind": "human_interactive"}
    return {
        "purpose": (getattr(session, "purpose_handle", "") or ""),
        "categories": categories,
        "tiers": tiers,
        "provenance": provenance,
        "origin": origin_dict,
        "risk_preference": getattr(session, "risk_preference_at_spawn", "cautious"),
        "reversibility": _reversibility_to_dict(
            getattr(session, "current_action_reversibility", None),
        ),
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


class ConfiguredDecisionInspector:
    """Wrap a builtin inspector with loader metadata such as failure mode."""

    def __init__(self, inspector: Any, *, failure_mode: str = "abstain") -> None:
        self._inspector = inspector
        self.failure_mode = failure_mode
        self.name = getattr(inspector, "name", type(inspector).__name__)

    def inspect(
        self,
        *,
        action: Any,
        session: Any,
        proposed_outcome: Any,
    ) -> Any:
        return self._inspector.inspect(
            action=action,
            session=session,
            proposed_outcome=proposed_outcome,
        )


class ScriptDecisionInspector:
    """Adapts a compiled `PolicyScript` to the `DecisionInspector`
    protocol. `inspect` is async (it awaits the host's `evaluate`, which
    runs the script off the event loop); the chokepoint awaits async
    inspectors. Builtins stay synchronous — both are supported.
    """

    def __init__(
        self,
        name: str,
        host: Any,
        script: Any,
        *,
        failure_mode: str = "abstain",
    ) -> None:
        self.name = name
        self._host = host
        self._script = script
        self.failure_mode = failure_mode

    async def inspect(
        self,
        *,
        action: Any,
        session: Any,
        proposed_outcome: Any,
    ) -> DecisionRelax | DecisionTighten | None:
        action_dict = _action_to_dict(action)
        session_dict = _session_to_dict(session)
        session_dict["reversibility"] = action_dict["reversibility"]
        outcome = await self._host.evaluate(
            self._script,
            action=action_dict,
            session=session_dict,
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
    if runtime == "python-reference" and not _unsafe_python_reference_allowed():
        raise DecisionInspectorConfigError(
            f"decision_inspectors[{index}]: runtime 'python-reference' is not a security "
            "boundary and is disabled by default; set "
            "CAPDEP_ALLOW_UNSANDBOXED_POLICY_SCRIPTS=1 only for tests or local prototyping",
        )
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
            f"decision_inspectors[{index}]: script {name!r} ({runtime}) failed to compile: {e}",
        ) from e
    return ScriptDecisionInspector(name, host, script, failure_mode=_failure_mode(entry))


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
            inspector = _load_builtin(i, entry)
            mode = _failure_mode(entry)
            inspectors.append(
                inspector
                if mode == "abstain"
                else ConfiguredDecisionInspector(inspector, failure_mode=mode),
            )
        elif "script" in entry or "source" in entry:
            inspectors.append(_load_script(i, entry, base_dir))
        else:
            raise DecisionInspectorConfigError(
                f"decision_inspectors[{i}] must declare one of 'builtin', 'script', or 'source'",
            )
    return tuple(inspectors)


# --- #305 — posture inspector_set application -------------------------
#
# A selected posture's `inspector_set` names which inspectors are ACTIVE.
# Canonical names are the snake_case builtin ids (the same tokens the
# `decision_inspectors:` config uses); script inspectors go by their
# configured name. Runtime builtin instances carry class-style `.name`
# attributes, so map them back to the canonical ids for matching.

_BUILTIN_CANONICAL_NAMES: dict[str, str] = {
    "SelfEgressRelaxer": "self_egress_relaxer",
    "AfterHoursPurchaseTightener": "after_hours_purchase_tightener",
}

# Default-parameterized builtins for a preset that names an inspector the
# operator has not configured. INVARIANT: every factory here must be
# tighten-or-inert with its defaults — anything in this dict is
# auto-instantiated UNCONFIGURED when a posture names it, so a default that
# relaxed anything would loosen policy without an operator declaration.
# Today: the tightener only ratchets stricter, and the relaxer with an empty
# self-address set relaxes nothing (inert until the operator parameterizes
# it via a `decision_inspectors:` entry).
_BUILTIN_DEFAULT_FACTORIES: dict[str, Any] = {
    "self_egress_relaxer": lambda: SelfEgressRelaxer(self_addresses=frozenset()),
    "after_hours_purchase_tightener": AfterHoursPurchaseTightener,
}


def canonical_inspector_name(inspector: Any) -> str:
    """The posture-facing name of a loaded inspector (snake_case builtin id,
    or the script's configured name)."""
    name = str(getattr(inspector, "name", type(inspector).__name__))
    return _BUILTIN_CANONICAL_NAMES.get(name, name)


def select_inspectors_for_posture(
    inspectors: tuple[Any, ...],
    inspector_set: tuple[str, ...],
) -> tuple[tuple[Any, ...], list[str]]:
    """Apply a posture's `inspector_set` to the loaded inspector tuple.

    Semantics (#305):
      - a loaded inspector whose canonical name is IN the set stays active
        (with its operator parameterization);
      - a loaded inspector NOT in the set is deactivated (e.g. `strict`
        drops a configured relaxer — tighteners only);
      - a name in the set with NO loaded match: a known builtin is
        instantiated with safe defaults (returned warning tells the operator
        to parameterize it), anything else is fail-closed — a typo'd preset
        must not silently ship with fewer inspectors than it declares.

    Returns (active_inspectors, warnings).
    """
    wanted = set(inspector_set)
    active = [i for i in inspectors if canonical_inspector_name(i) in wanted]
    matched = {canonical_inspector_name(i) for i in active}
    warnings: list[str] = []
    for name in inspector_set:  # preserve posture-declared order for defaults
        if name in matched:
            continue
        factory = _BUILTIN_DEFAULT_FACTORIES.get(name)
        if factory is None:
            raise DecisionInspectorConfigError(
                f"posture inspector_set names unknown inspector {name!r}; known "
                f"builtins: {sorted(_BUILTIN_DEFAULT_FACTORIES)}, plus any "
                "configured `decision_inspectors:` entry names.",
            )
        active.append(factory())
        warnings.append(
            f"posture inspector {name!r} has no `decision_inspectors:` entry; "
            "instantiated with safe defaults — add an entry to parameterize it.",
        )
    return tuple(active), warnings


# Exposed so the chokepoint can detect & await async inspectors without
# importing this module's adapter directly.
__all__ = [
    "DecisionInspectorConfigError",
    "DecisionInspectorScriptError",
    "ScriptDecisionInspector",
    "canonical_inspector_name",
    "isawaitable",
    "load_decision_inspectors",
    "select_inspectors_for_posture",
]
