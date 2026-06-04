"""Daemon RPCs for the bundled-approval workflow.

Three methods:

  - `programmatic.bundle_dry_run` — runs the bundle collector;
    returns the full WorkflowImpact as JSON.
  - `programmatic.bundle_execute` — given an approved bundle (the
    same JSON shape, with gates marked approved) plus the source,
    re-runs the program with each gate dispatched via a purpose-
    limited session.
  - `programmatic.bundle_run` — convenience: dry-run, auto-approve
    every gate, execute. Equivalent to `--auto-approve` on the CLI.
    Useful for CI when you trust the program to a fixed state.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from capabledeputy.app import App
from capabledeputy.approval.bundle import (
    BundledApproval,
    BundleExpiredError,
    GateState,
    WorkflowImpact,
    WorkflowStep,
    render_impact_tree,
)
from capabledeputy.daemon.handlers import Handler
from capabledeputy.programmatic import (
    dry_run_for_bundle,
    execute_with_approved_bundle,
)


def _impact_from_dict(d: dict[str, Any]) -> WorkflowImpact:
    """Reconstruct an WorkflowImpact from its to_dict() form.

    Roadmap v2 #6 — `expires_at` round-trips so dispatch can
    refuse stale bundles. Missing key → None (legacy bundle from
    pre-v2 daemon), which leaves the post-init TTL alone. To keep
    a deserialized bundle truly immortal, the caller would supply
    `expires_at: null` from a daemon that ran with TTL=0.
    """
    expires_at_raw = d.get("expires_at")
    expires_at = datetime.fromisoformat(expires_at_raw) if expires_at_raw else None
    created_at_raw = d.get("created_at")
    impact_kwargs: dict[str, Any] = {
        "bundle_id": UUID(d["bundle_id"]),
        "program_hash": str(d["program_hash"]),
    }
    if created_at_raw is not None:
        impact_kwargs["created_at"] = datetime.fromisoformat(created_at_raw)
    if expires_at is not None:
        impact_kwargs["expires_at"] = expires_at
    return WorkflowImpact(
        **impact_kwargs,
        steps=[
            WorkflowStep(
                step_index=s["step_index"],
                tool_name=s["tool"],
                args=s["args"],
                arg_labels=frozenset(s["arg_labels"]),
                decision=s["decision"],
                inherent_labels=frozenset(s["inherent_labels"]),
                rule=s.get("rule"),
                reason=s.get("reason"),
                line=s.get("line"),
            )
            for s in d.get("steps", [])
        ],
        gates=[
            BundledApproval(
                step_index=g["step_index"],
                tool_name=g["tool"],
                args=g["args"],
                arg_labels=frozenset(g["arg_labels"]),
                rule=g.get("rule"),
                reason=g.get("reason"),
                state=GateState(g["state"]),
            )
            for g in d.get("gates", [])
        ],
    )


def make_bundle_handlers(app: App) -> dict[str, Handler]:
    async def bundle_dry_run(params: dict[str, Any]) -> dict[str, Any]:
        source = str(params["source"])
        impact = await dry_run_for_bundle(source, app.registry)
        return {
            "impact": impact.to_dict(),
            "rendered": render_impact_tree(impact),
            "is_approvable": impact.is_approvable,
        }

    async def bundle_execute(params: dict[str, Any]) -> dict[str, Any]:
        source = str(params["source"])
        session_id = UUID(params["session_id"])
        impact = _impact_from_dict(params["impact"])
        if impact.is_expired():
            err = BundleExpiredError(impact.bundle_id, impact.expires_at)
            return {
                "ok": False,
                "error": str(err),
                "error_code": "bundle_expired",
                "bundle_id": str(impact.bundle_id),
                "expires_at": impact.expires_at.isoformat() if impact.expires_at else None,
                "n_steps": 0,
                "return_value": None,
            }
        result = await execute_with_approved_bundle(
            source,
            impact,
            session_id=session_id,
            tool_client=app.tool_client,
            graph=app.graph,
            registry=app.registry,
            audit=app.audit,
        )
        return {
            "ok": result.error is None,
            "error": result.error,
            "n_steps": len(result.tool_calls),
            "return_value": (
                None
                if result.return_value is None
                else {
                    "raw": result.return_value.raw,
                    "labels": sorted(label.value for label in result.return_value.labels),
                }
            ),
        }

    async def bundle_run(params: dict[str, Any]) -> dict[str, Any]:
        """Dry-run + approve-all + execute in one shot. CI-friendly."""
        source = str(params["source"])
        session_id = UUID(params["session_id"])
        impact = await dry_run_for_bundle(source, app.registry)
        if not impact.is_approvable:
            return {
                "ok": False,
                "rendered": render_impact_tree(impact),
                "error": "bundle is not approvable",
                "impact": impact.to_dict(),
            }
        approved = impact.approve_all()
        result = await execute_with_approved_bundle(
            source,
            approved,
            session_id=session_id,
            tool_client=app.tool_client,
            graph=app.graph,
            registry=app.registry,
            audit=app.audit,
        )
        return {
            "ok": result.error is None,
            "error": result.error,
            "n_steps": len(result.tool_calls),
            "rendered": render_impact_tree(approved),
        }

    return {
        "programmatic.bundle_dry_run": bundle_dry_run,
        "programmatic.bundle_execute": bundle_execute,
        "programmatic.bundle_run": bundle_run,
    }
