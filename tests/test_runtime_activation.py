"""Composition — runtime activation (the wiring spine).

Pins the daemon-side wiring of the operator-curated configs into the
LabeledToolClient + SessionGraph. Without this, the v2 four-axis
pipeline only activates in test fixtures that hand-construct
PolicyContext — it does NOT defend a real running deployment.

What this test asserts:
  1. build_policy_context_from_configs() loads every config file
     in the repo's configs/ directory and produces a populated
     PolicyContext.
  2. App(policy_context=...) threads it through to
     LabeledToolClient and SessionGraph.
  3. A dispatch via App.tool_client invokes engine.decide() with
     the v2 args populated; an operator-curated rule fires when
     applicable. This is the operational manifest that the system
     defends at runtime, not just in test fixtures.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from capabledeputy.app import App
from capabledeputy.daemon.lifecycle import build_policy_context_from_configs
from capabledeputy.policy.context import PolicyContext
from capabledeputy.policy.envelope import RiskPreference

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CONFIGS_DIR = _REPO_ROOT / "configs"


def test_build_policy_context_loads_from_repo_configs() -> None:
    """The repo's configs/ directory is a complete v0.9 config set —
    build_policy_context_from_configs() must succeed against it."""
    ctx, _purposes = build_policy_context_from_configs(_CONFIGS_DIR)
    assert isinstance(ctx, PolicyContext)
    # The shipped configs/rules.yaml has the 3 example rules from T049.
    assert ctx.rules_v2 is not None
    assert len(ctx.rules_v2.rules) >= 1
    # Bindings are opt-in: empty source_bindings.yaml ⇒ ctx.bindings
    # is None (faithful fail-closed without locking out a fresh
    # install). As soon as the operator declares any binding the
    # field populates and FR-023 fail-closed kicks in.
    assert ctx.bindings is None
    assert ctx.override_policies is not None
    assert ctx.override_grants is not None
    assert ctx.handle_store is not None
    assert ctx.envelope_set is not None
    assert ctx.risk_preference in (
        RiskPreference.CAUTIOUS,
        RiskPreference.BALANCED,
        RiskPreference.PERMISSIVE,
    )
    # Purposes registry may be None if configs/purposes.yaml has
    # `purposes: []` — that's a valid empty registry.


def test_app_threads_policy_context_into_tool_client(tmp_path: Any) -> None:
    """An App constructed with policy_context has the bus wired all
    the way through to LabeledToolClient."""
    ctx, purposes = build_policy_context_from_configs(_CONFIGS_DIR)
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        policy_context=ctx,
        purposes=purposes,
        enable_policy_preview=False,
    )
    assert app.policy_context is ctx
    assert app.purposes is purposes
    # Verify the dispatcher actually holds the context.
    assert app.tool_client._policy_context is ctx
    # Verify the session graph received the purposes registry.
    assert app.graph._purposes is purposes


def test_app_without_policy_context_is_v07_back_compat(tmp_path: Any) -> None:
    """Default App (no policy_context) behaves exactly as v0.7 —
    the dispatcher's policy context is None; the v2 pipeline is
    dormant."""
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        enable_policy_preview=False,
    )
    assert app.policy_context is None
    assert app.purposes is None
    assert app.tool_client._policy_context is None
    assert app.graph._purposes is None


@pytest.mark.asyncio
async def test_runtime_activated_email_send_denies_via_reversibility_gate(
    tmp_path: Any,
) -> None:
    """End-to-end runtime: the production `email.send` tool now
    declares social_commitment=True + effect_class=social.send_email.
    With the runtime-activated PolicyContext, the reversibility gate
    fires and refuses regardless of capability holdings."""
    from capabledeputy.policy.capabilities import (
        Capability,
        CapabilityKind,
        CapabilityOrigin,
    )

    ctx, purposes = build_policy_context_from_configs(_CONFIGS_DIR)
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        policy_context=ctx,
        purposes=purposes,
        enable_policy_preview=False,
    )
    await app.startup()
    s = await app.graph.new()
    s = s.__class__(
        id=s.id,
        parent=s.parent,
        status=s.status,
        label_state=s.label_state,
        axis_d=s.axis_d,
        capability_set=frozenset(
            {
                Capability(
                    kind=CapabilityKind.SEND_EMAIL,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
            },
        ),
        history=s.history,
        declassification_log=s.declassification_log,
        created_at=s.created_at,
        updated_at=s.updated_at,
        owner=s.owner,
        intent=s.intent,
    )
    app.graph._sessions[s.id] = s

    outcome = await app.tool_client.call_tool(
        s.id,
        "email.send",
        {"to": "alice@example.com", "subject": "x", "body": "y"},
    )
    # With the live PolicyContext, the dispatch is GATED — without the
    # PolicyContext threading, the same dispatch would have ALLOWED
    # (legacy v0.7 path). Email is irreversible communication egress, so
    # under the amended FR-019 it routes to human APPROVAL by default
    # (binding-unbound could also fire under FR-023 fail-closed for the
    # stub configs). What this pins is that the runtime IS now defending.
    from capabledeputy.policy.rules import Decision

    assert outcome.decision != Decision.ALLOW


@pytest.mark.asyncio
async def test_legacy_app_without_policy_context_allows_same_dispatch(
    tmp_path: Any,
) -> None:
    """Sanity contrast: the same dispatch on a back-compat App (no
    PolicyContext) ALLOWS. This is what makes the previous test the
    runtime-activation invariant — the difference between the two
    Apps is the wiring spine."""
    from capabledeputy.policy.capabilities import (
        Capability,
        CapabilityKind,
        CapabilityOrigin,
    )
    from capabledeputy.policy.rules import Decision

    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        enable_policy_preview=False,
    )
    await app.startup()
    s = await app.graph.new()
    s = s.__class__(
        id=s.id,
        parent=s.parent,
        status=s.status,
        label_state=s.label_state,
        axis_d=s.axis_d,
        capability_set=frozenset(
            {
                Capability(
                    kind=CapabilityKind.SEND_EMAIL,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
            },
        ),
        history=s.history,
        declassification_log=s.declassification_log,
        created_at=s.created_at,
        updated_at=s.updated_at,
        owner=s.owner,
        intent=s.intent,
    )
    app.graph._sessions[s.id] = s
    outcome = await app.tool_client.call_tool(
        s.id,
        "email.send",
        {"to": "alice@example.com", "subject": "x", "body": "y"},
    )
    # No PolicyContext ⇒ v0.7 legacy path ⇒ ALLOW (the matching cap
    # is sufficient; reversibility / binding gates are dormant).
    assert outcome.decision == Decision.ALLOW
