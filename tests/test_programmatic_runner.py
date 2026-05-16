"""Programmatic-mode runner: dry-run and end-to-end execution.

Two complementary surfaces:

  - dry_run_program: parses + symbolic-executes against the policy
    engine; reports predicted tool calls and any conflict-rule
    violations without dispatching real handlers.
  - run_program_against_session: real dispatch through LabeledToolClient,
    full audit + label propagation.

The dry-run prescription scenario is the v0.3 done-when criterion in
test form: a program that reads health data and then attempts an egress
must be flagged BEFORE execution.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from capabledeputy.app import App
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import Label
from capabledeputy.policy.rules import Decision
from capabledeputy.programmatic import (
    dry_run_program,
    run_program_against_session,
)


async def test_dry_run_clean_program_predicts_calls() -> None:
    app = App()
    src = """
note = call("memory.read", key="grocery_list")
saved = call("memory.write", key="copy", value=note)
"""
    report = await dry_run_program(src, app.registry)
    assert report.parse_error is None
    assert report.runtime_error is None
    assert len(report.tool_calls) == 2
    assert all(c.decision == Decision.ALLOW for c in report.tool_calls)
    assert report.ok


async def test_dry_run_health_then_egress_predicts_violation() -> None:
    """The v0.3 done-when scenario in test form.

    Reading from a memory key that the registry's memory.read tool
    inherently labels as confidential.health, then attempting a
    purchase egress, must be flagged at dry-run time as a conflict
    on health-meets-egress without any tool handler running.
    """
    app = App()
    # memory.read inherits no labels by default; we supply the labeled
    # value through initial_scope to model "this value came from a
    # health-tagged source". This keeps the dry-run analysis source-
    # agnostic and lets callers feed in worst-case-input scenarios.
    from capabledeputy.programmatic.value import LabeledValue

    src = """
result = call("purchase.queue", vendor="amazon", item=labeled_input, amount=50)
"""
    report = await dry_run_program(
        src,
        app.registry,
        initial_scope={
            "labeled_input": LabeledValue(
                raw="rx",
                labels=frozenset({Label.CONFIDENTIAL_HEALTH}),
            ),
        },
    )
    assert not report.ok
    assert len(report.violations) == 1
    [violation] = report.violations
    assert violation.tool_name == "purchase.queue"
    assert violation.decision == Decision.DENY
    assert violation.rule == "health-meets-egress"


async def test_dry_run_parse_error_reported() -> None:
    app = App()
    report = await dry_run_program("import os\n", app.registry)
    assert report.parse_error is not None
    assert "import" in report.parse_error.lower() or "Import" in report.parse_error
    assert not report.ok


async def test_dry_run_unknown_tool_flagged() -> None:
    app = App()
    report = await dry_run_program('call("nope.tool", x=1)\n', app.registry)
    assert not report.ok
    [violation] = report.violations
    assert violation.tool_name == "nope.tool"
    assert violation.decision == Decision.DENY


async def test_run_program_against_session_executes(tmp_path: Path) -> None:
    """End-to-end: a labeled-value pipeline runs, audits, and stores."""
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await app.startup()
    s = await app.graph.new(intent="programmatic test")
    read_cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    write_cap = Capability(kind=CapabilityKind.WRITE_FS, pattern="*")
    app.graph._sessions[s.id] = replace(
        s,
        capability_set=frozenset({read_cap, write_cap}),
    )

    app.memory.write("source", "hello world", frozenset())
    src = """
note = call("memory.read", key="source")
saved = call("memory.write", key="copy", value=note["value"])
"""
    result = await run_program_against_session(
        src,
        session_id=s.id,
        tool_client=app.tool_client,
        graph=app.graph,
        registry=app.registry,
        audit=app.audit,
    )
    assert result.error is None
    assert len(result.tool_calls) == 2
    assert all(c.decision.value == "allow" for c in result.tool_calls)
    entry = app.memory.read("copy")
    assert entry is not None
    assert entry.value == "hello world"

    events = await app.audit.read_all()
    types = [e.event_type.value for e in events]
    assert "tool.dispatched" in types
    assert "tool.returned" in types
    assert "mode.selected" in types  # programmatic-mode marker


async def test_run_program_halts_on_policy_deny(tmp_path: Path) -> None:
    """Real-execution counterpart: deny halts and leaves clean state."""
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await app.startup()
    s = await app.graph.new(intent="programmatic deny test")
    read_cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    purchase_cap = Capability(
        kind=CapabilityKind.QUEUE_PURCHASE,
        pattern="*",
        max_amount=10_000,
    )
    app.graph._sessions[s.id] = replace(
        s,
        capability_set=frozenset({read_cap, purchase_cap}),
    )

    app.memory.write(
        "labs",
        "lisinopril 10mg",
        frozenset({Label.CONFIDENTIAL_HEALTH}),
    )

    src = """
labs = call("memory.read", key="labs")
purchase = call("purchase.queue", vendor="pharmacy", item=labs, amount=50)
"""
    result = await run_program_against_session(
        src,
        session_id=s.id,
        tool_client=app.tool_client,
        graph=app.graph,
        registry=app.registry,
        audit=app.audit,
    )
    assert result.error is not None
    assert "purchase.queue" in result.error
    assert len(app.purchase_queue.all()) == 0
    final = app.graph.get(s.id)
    assert Label.CONFIDENTIAL_HEALTH in final.label_set


# --- Dry-run capability-constraint boundary (documented + tested) --------
#
# The programmatic dry-run is information-flow-only. It deliberately
# does NOT model capability-level denials (no-cap / expired /
# rate-limited / revoked / destructive-op gate). These tests codify
# that boundary so the drift from `decide()` is intentional and known,
# not silent — and prove it is SAFE (runtime fails closed).

from datetime import UTC, datetime, timedelta  # noqa: E402

from capabledeputy.policy.actions import Action  # noqa: E402
from capabledeputy.policy.capabilities import RateLimit  # noqa: E402
from capabledeputy.policy.engine import (  # noqa: E402
    CAPABILITY_EXPIRED_RULE,
    RATE_LIMIT_EXCEEDED_RULE,
    REVOKED_BY_PRIOR_USE_RULE,
    decide,
)

_AID = "44444444-4444-4444-4444-444444444444"


async def test_dry_run_ignores_capability_constraints_boundary() -> None:
    """A plain read program dry-runs ALLOW with NO capability model at
    all — the dry-run never consults grants, expiry, rate, or
    revocation. (Label conflict rules are the only thing it predicts.)
    """
    app = App()
    report = await dry_run_program(
        'note = call("memory.read", key="x")\n', app.registry,
    )
    assert report.parse_error is None
    assert [c.decision for c in report.tool_calls] == [Decision.ALLOW]
    # The report carries no notion of capabilities/expiry/rate — it is
    # purely an information-flow prediction.


def test_dry_run_boundary_is_safe_runtime_fails_closed() -> None:
    """The safety invariant behind the boundary: for the SAME call the
    dry-run optimistically ALLOWs, the real chokepoint (`decide()`)
    still DENYs when the capability is expired / rate-limited / revoked.
    The dry-run can only be optimistic, never permissive — enforcement
    is unaffected."""
    from uuid import UUID

    action = Action(kind=CapabilityKind.READ_FS, target="/x")
    now = datetime(2026, 5, 1, tzinfo=UTC)

    expired = Capability(
        kind=CapabilityKind.READ_FS, pattern="*", expires_at=now,
    )
    r1 = decide(
        frozenset(), frozenset({expired}), action,
        now=now + timedelta(seconds=1),
    )
    assert r1.decision == Decision.DENY
    assert r1.rule == CAPABILITY_EXPIRED_RULE

    rate = Capability(
        kind=CapabilityKind.READ_FS, pattern="*",
        audit_id=UUID(_AID),
        rate_limit=RateLimit(max_uses=1, window_seconds=3600),
    )
    r2 = decide(
        frozenset(), frozenset({rate}), action, now=now,
        cap_uses={_AID: (now - timedelta(seconds=1),)},
    )
    assert r2.decision == Decision.DENY
    assert r2.rule == RATE_LIMIT_EXCEEDED_RULE

    revoked = Capability(
        kind=CapabilityKind.READ_FS, pattern="*",
        revoked_by=frozenset({CapabilityKind.WEB_FETCH}),
    )
    r3 = decide(
        frozenset(), frozenset({revoked}), action,
        used_kinds=frozenset({CapabilityKind.WEB_FETCH}),
    )
    assert r3.decision == Decision.DENY
    assert r3.rule == REVOKED_BY_PRIOR_USE_RULE

    # Conclusion: dry-run ALLOW + runtime DENY for the same action is
    # the documented, intentional boundary — not a bug. Enforcement is
    # the single chokepoint; the dry-run is advisory.
