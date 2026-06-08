"""Shared deterministic policy-engine harness.

No real LLM, no network. A scenario primes a FakeLLMClient with a
hardcoded tool-call cassette, runs it through the *real* agent loop and
the *real* deterministic policy engine, then asserts the engine's
decision (and optionally the rule) for each tool call against a
hardcoded expectation.

The themed `scripts/policy_*.py` files import this module, declare a
list of `Scenario`s, and call `run_suite(...)`. Same inputs -> same
decisions -> same exit code (0 = all met, 1 = any mismatch).
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from capabledeputy.app import App
from capabledeputy.daemon.agent_handlers import make_agent_handlers
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import FinishReason, LLMResponse, ToolCall
from capabledeputy.policy.capabilities import Capability
from capabledeputy.policy.labels import tags_for_labels_strings


def tc(call_id: str, name: str, **args: object) -> ToolCall:
    """One scripted tool call."""
    return ToolCall(id=call_id, name=name, args=dict(args))


def tool_turn(content: str, *calls: ToolCall) -> LLMResponse:
    """An assistant turn that invokes one or more tools."""
    return LLMResponse(
        content=content,
        tool_calls=calls,
        finish_reason=FinishReason.TOOL_CALLS,
    )


def final(content: str = "done") -> LLMResponse:
    """The terminal assistant turn (no tool calls)."""
    return LLMResponse(content=content, finish_reason=FinishReason.STOP)


@dataclass(frozen=True)
class Expect:
    """One asserted policy outcome for one tool call, in order."""

    tool: str
    decision: str  # "allow" | "deny" | "require_approval"
    rule_contains: str | None = None  # substring check; None = don't care


@dataclass
class Scenario:
    name: str
    why: str
    caps: frozenset[Capability]
    responses: list[LLMResponse]
    expect: list[Expect]
    session_labels: frozenset[str] = frozenset()
    # Optional seeding hook: receives the started App; use it to write
    # labeled memory, add inbox messages, create calendar events, etc.
    pre: Callable[[App], None] | None = field(default=None)
    # Optional quarantined-LLM cassette. Required only by scenarios that
    # exercise quarantined.extract (the dual-LLM declassifier path); the
    # planner LLM never sees this, and enforcement is unaffected.
    quarantined: list[LLMResponse] | None = field(default=None)
    # --- v0.16 feature wiring (the policy-refinement + labeling layer) ---
    # `decision_inspectors`: a daemon-config-shaped list (builtins / Starlark
    #   / inline source) compiled through the real loader into PolicyContext
    #   (#46/#47/#48). `fs_label_rules`: raw fs_label_rules.yaml rules so a
    #   read attaches Axis-A category labels (#5). `relationship_groups`:
    #   {group_id: [member_ids]} so relationship-aware inspectors resolve
    #   (#47). Default-empty ⇒ exact pre-v0.16 behavior.
    decision_inspectors: list[dict[str, Any]] = field(default_factory=list)
    fs_label_rules: list[dict[str, Any]] | None = field(default=None)
    relationship_groups: dict[str, list[str]] | None = field(default=None)


async def run_scenario(sc: Scenario) -> list[str]:
    """Run one scenario; return failure strings (empty list = pass)."""
    from dataclasses import replace

    failures: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # v0.16 feature wiring — build a PolicyContext + fs labeler from the
        # scenario's declarations so the harness exercises the real
        # decision-inspector + labeling path end-to-end (not just engine
        # invariants). Empty declarations ⇒ policy_context=None (legacy).
        policy_context = None
        if sc.decision_inspectors or sc.relationship_groups:
            import dataclasses

            from capabledeputy.daemon.lifecycle import build_policy_context_from_configs
            from capabledeputy.policy.decision_inspector_loader import (
                load_decision_inspectors,
            )

            # Build the PolicyContext from the REAL shipped configs/ so the
            # scenario exercises the actual operator policy (rules,
            # envelopes, reversibility) composed WITH the inspector layer —
            # not a hand-rolled context. Then inject the scenario's
            # inspectors + relationship groups.
            policy_context, _ = build_policy_context_from_configs(
                state_db_path=tmp / "state.db",
            )
            inspectors = load_decision_inspectors(
                {"decision_inspectors": sc.decision_inspectors},
            )
            rg = policy_context.relationship_groups
            if sc.relationship_groups:
                from capabledeputy.policy.relationships import (
                    RelationshipGroup,
                    RelationshipGroups,
                )

                rg = RelationshipGroups(
                    groups={
                        gid: RelationshipGroup(
                            group_id=gid,
                            member_principal_ids=frozenset(members),
                        )
                        for gid, members in sc.relationship_groups.items()
                    },
                )
            policy_context = dataclasses.replace(
                policy_context,
                decision_inspectors=inspectors,
                relationship_groups=rg,
            )

        fs_labeler = None
        if sc.fs_label_rules is not None:
            from capabledeputy.policy.fs_labeling import parse_fs_label_rules

            fs_labeler = parse_fs_label_rules(sc.fs_label_rules)

        app = App(
            state_db_path=tmp / "state.db",
            audit_log_path=tmp / "audit.jsonl",
            llm_client=FakeLLMClient(sc.responses),
            quarantined_llm=(FakeLLMClient(sc.quarantined) if sc.quarantined is not None else None),
            policy_context=policy_context,
            fs_labeler=fs_labeler,
        )
        await app.startup()

        # Issue #52 — restricted-tier sessions (e.g. confidential.health /
        # .financial, now restricted via the catalog, #50) require a
        # Pattern ③/⑤ mode or the per-turn select_mode fails closed
        # (FR-047). A real restricted session only exists because it
        # passed the spawn-time gate with such a mode available; this
        # harness seeds label_state out-of-band, bypassing that gate, so
        # we register an inert handle-aware tool here to stand in for it.
        # REFERENCE mode runs the normal turn loop (no reversibility
        # lift), so the engine decisions under test are unchanged.
        from capabledeputy.policy.effect_class import EffectClass, Operation
        from capabledeputy.tools.registry import ToolDefinition

        async def _handle_noop(_args: dict) -> dict:
            return {}

        app.registry._tools.setdefault(
            "ref.noop",
            ToolDefinition(
                name="ref.noop",
                description="inert handle-aware tool (harness FR-047 mode floor)",
                capability_kind="EXECUTE",
                handler=_handle_noop,
                operations=(Operation(EffectClass.FETCH, subtype="ref.noop"),),
                risk_ids=("RISK-HEALTH-LEAK",),
                accepts_handles=True,
                handle_arg_names=("ref",),
            ),
        )

        if sc.pre is not None:
            sc.pre(app)

        s = await app.graph.new(intent=f"harness:{sc.name}")
        seed = tags_for_labels_strings(sc.session_labels)
        app.graph._sessions[s.id] = replace(
            s,
            capability_set=sc.caps,
            label_state=seed,
        )

        handlers = make_agent_handlers(app)
        result = await handlers["session.send"](
            {"session_id": str(s.id), "message": f"run {sc.name}"},
        )

        outcomes = result.get("tool_outcomes", [])
        if len(outcomes) != len(sc.expect):
            failures.append(
                f"expected {len(sc.expect)} tool outcome(s), got "
                f"{len(outcomes)}: {[o.get('tool_name') for o in outcomes]}",
            )

        for i, exp in enumerate(sc.expect):
            if i >= len(outcomes):
                failures.append(f"#{i} missing: expected {exp.tool} {exp.decision}")
                continue
            o = outcomes[i]
            got_tool = o.get("tool_name")
            got_decision = o.get("decision")
            got_rule = o.get("rule")
            if got_tool != exp.tool:
                failures.append(f"#{i} tool: expected {exp.tool}, got {got_tool}")
            if got_decision != exp.decision:
                failures.append(
                    f"#{i} {exp.tool} decision: expected {exp.decision}, "
                    f"got {got_decision} (rule={got_rule})",
                )
            if exp.rule_contains and exp.rule_contains not in (got_rule or ""):
                failures.append(
                    f"#{i} {exp.tool} rule: expected to contain "
                    f"'{exp.rule_contains}', got '{got_rule}'",
                )
    return failures


async def run_suite(title: str, scenarios: list[Scenario]) -> int:
    """Run every scenario, print a report, return a process exit code."""
    bar = "=" * 68
    print(bar)
    print(f"Policy harness — {title}  ({len(scenarios)} scenarios, no real LLM)")
    print(bar)
    total_fail = 0
    for sc in scenarios:
        failures = await run_scenario(sc)
        status = "PASS" if not failures else "FAIL"
        print(f"\n[{status}] {sc.name}")
        print(f"       {sc.why}")
        for exp in sc.expect:
            tail = f"  (rule~='{exp.rule_contains}')" if exp.rule_contains else ""
            print(f"       expect: {exp.tool:<22} -> {exp.decision}{tail}")
        for f in failures:
            print(f"       MISMATCH: {f}")
        total_fail += len(failures)

    print("\n" + bar)
    if total_fail:
        print(f"RESULT: FAIL — {title}: {total_fail} mismatch(es)")
        return 1
    print(f"RESULT: PASS — {title}: all {len(scenarios)} scenarios as scripted")
    return 0
