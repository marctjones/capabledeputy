"""capdep policy: inspect and simulate the policy engine."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

import anyio
import typer
from rich.console import Console
from rich.table import Table

from capabledeputy.ipc.client import DaemonClient
from capabledeputy.ipc.socket_path import default_socket_path
from capabledeputy.policy.resolution import (
    ResolutionError,
    load_categories,
    load_profiles,
    resolve_tier,
)

policy_app = typer.Typer(
    help="Inspect and test the policy engine.",
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)


@policy_app.command("resolve")
def resolve_command(
    category: Annotated[str, typer.Argument(help="Axis A category id (e.g., health).")],
    profile: Annotated[
        str,
        typer.Argument(help="Comma-separated profile ids (e.g., clinician,general)."),
    ],
    labels_file: Annotated[
        Path,
        typer.Option(
            "--labels",
            help="Path to labels.yaml (default: configs/labels.yaml).",
        ),
    ] = Path("configs/labels.yaml"),
    profiles_file: Annotated[
        Path,
        typer.Option(
            "--profiles",
            help="Path to profiles.yaml (default: configs/profiles.yaml).",
        ),
    ] = Path("configs/profiles.yaml"),
) -> None:
    """Deterministically resolve a sensitivity tier for a (category,
    profile-set) pair. 003 US1 demo (FR-007, SC-002). No LLM in the
    path; same inputs always produce same output."""
    try:
        categories = load_categories(labels_file)
        profiles = load_profiles(profiles_file)
    except ResolutionError as e:
        err_console.print(f"[red]config error:[/red] {e}")
        raise typer.Exit(code=2) from e

    pids = tuple(p.strip() for p in profile.split(",") if p.strip())
    try:
        result = resolve_tier(
            category_id=category,
            profile_ids=pids,
            categories=categories,
            profiles=profiles,
        )
    except ResolutionError as e:
        err_console.print(f"[red]resolution error:[/red] {e}")
        raise typer.Exit(code=1) from e

    console.print(f"[bold]tier:[/bold] {result.tier.value}")
    console.print(f"[dim]rationale: {result.rationale}[/dim]")
    if result.contributing_profile_ids:
        console.print(
            f"[dim]profiles consulted: {','.join(result.contributing_profile_ids)}[/dim]",
        )


def _client() -> DaemonClient:
    return DaemonClient(default_socket_path())


def _call(method: str, params: dict[str, Any] | None = None) -> Any:
    return anyio.run(_client().call, method, params or {})


def _parse_capability_spec(spec: str) -> dict[str, Any]:
    kind, sep, pattern = spec.partition(":")
    if not sep or not pattern:
        raise typer.BadParameter(
            f"invalid capability spec '{spec}', expected KIND:pattern",
        )
    return {
        "kind": kind,
        "pattern": pattern,
        "expiry": "session",
        "origin": "system_default",
        "audit_id": str(uuid4()),
    }


@policy_app.command("show")
def policy_show(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of tables"),
    ] = False,
) -> None:
    """Show the current label set, capability kinds, and conflict rules."""
    result = _call("policy.show")

    if json_output:
        console.print_json(data=result)
        return

    kind_table = Table(title=f"Capability kinds ({len(result['capability_kinds'])})")
    kind_table.add_column("Kind")
    for kind in result["capability_kinds"]:
        kind_table.add_row(kind)
    console.print(kind_table)


# Issue #53 — make the Biba gap (and every other scoped deviation) loud
# and operator-discoverable, so nobody assumes a model is more complete
# than it is. Source of truth: docs/security-models.md +
# docs/security-alignment-assessment.md. (model, scope, status).
_MODEL_COVERAGE: tuple[tuple[str, str, str], ...] = (
    (
        "Bell-LaPadula (confidentiality)",
        "read-up containment: data a session touched can't egress below its level",
        "scoped",
    ),
    (
        "Biba (integrity)",
        "ONE DIRECTION ONLY: low-integrity (untrusted) data is blocked from "
        "flowing/writing up. There is NO integrity-clearance / no-read-down. "
        "Do NOT assume full Biba duality.",
        "gap",
    ),
    (
        "Brewer-Nash (conflict-of-interest)",
        "session-scoped conflict invariants (not a global per-principal history)",
        "scoped",
    ),
    (
        "Clark-Wilson (well-formed transactions)",
        "declassification / cross-session merge / egress are gated transactions",
        "core",
    ),
    (
        "Object-capability (confused-deputy)",
        "unforgeable tokens held by the runtime; authority only attenuates",
        "core",
    ),
    (
        "Information-flow control (Denning)",
        "INTRANSITIVE noninterference; transitive NI is undecidable and a non-goal",
        "scoped",
    ),
)


@policy_app.command("models")
def policy_models() -> None:
    """Show which security models are enforced and — loudly — where each
    is scoped or has a known gap (Issue #53). Use this before assuming a
    model is fully covered; Biba in particular is one-direction only."""
    status_style = {"core": "green", "scoped": "yellow", "gap": "red"}
    table = Table(title="Security-model coverage (scope is honest, not aspirational)")
    table.add_column("Model")
    table.add_column("Status")
    table.add_column("Scope / known gap")
    for model, scope, status in _MODEL_COVERAGE:
        style = status_style.get(status, "white")
        table.add_row(model, f"[{style}]{status.upper()}[/{style}]", scope)
    console.print(table)
    err_console.print(
        "[bold red]⚠ Biba is one-direction only[/bold red] — untrusted data is "
        "kept from flowing up, but there is no integrity-clearance / no-read-down. "
        "See docs/security-models.md and docs/security-alignment-assessment.md.",
    )


@policy_app.command("validate")
def policy_validate() -> None:
    """Validate the active policy bundle's invariants."""
    result = _call("policy.validate")
    if result["valid"]:
        console.print("[green]policy is valid[/green]")
        return
    console.print("[red]policy is invalid[/red]")
    for err in result["errors"]:
        err_console.print(f"  - {err}")
    raise typer.Exit(code=1)


@policy_app.command("explain")
def policy_explain(
    session_id: Annotated[
        str | None,
        typer.Option("--session", help="Filter to one session id"),
    ] = None,
    audit_id: Annotated[
        str | None,
        typer.Option("--audit-id", help="Explain one policy decision audit id"),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of rendered output"),
    ] = False,
) -> None:
    """Explain the most recent matching daemon policy decision."""
    params: dict[str, Any] = {}
    if session_id:
        params["session_id"] = session_id
    if audit_id:
        params["audit_id"] = audit_id
    result = _call("policy.explain", params)
    if json_output:
        console.print_json(data=result)
        return
    if not result.get("found"):
        message = result.get("message", "No matching policy decision.")
        err_console.print(f"[yellow]{message}[/yellow]")
        raise typer.Exit(code=1)
    decision = result.get("decision", "")
    color = {
        "allow": "green",
        "deny": "red",
        "require_approval": "yellow",
    }.get(decision, "white")
    console.print(f"[{color}]decision: {decision}[/{color}]")
    if result.get("rule"):
        console.print(f"rule: {result['rule']}")
    if result.get("reason"):
        console.print(f"reason: {result['reason']}")
    console.print(result.get("plain_english", ""))


@policy_app.command("test")
def policy_test(
    action: Annotated[str, typer.Option("--action", help="Capability kind, e.g. SEND_EMAIL")],
    target: Annotated[str, typer.Option("--target", help="Action target (path/recipient/url)")],
    amount: Annotated[
        int | None,
        typer.Option("--amount", help="For QUEUE_PURCHASE actions"),
    ] = None,
    labels: Annotated[
        list[str] | None,
        typer.Option("--label", help="Label in the session (repeatable)"),
    ] = None,
    capability: Annotated[
        list[str] | None,
        typer.Option(
            "--capability",
            help="Capability KIND:pattern (repeatable)",
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a rendered view"),
    ] = False,
) -> None:
    """Simulate a policy decision for a given session state and action."""
    cap_specs = [_parse_capability_spec(spec) for spec in (capability or [])]
    params: dict[str, Any] = {
        "action_kind": action,
        "target": target,
        "labels": labels or [],
        "capabilities": cap_specs,
    }
    if amount is not None:
        params["amount"] = amount

    result = _call("policy.test", params)

    if json_output:
        console.print_json(data=result)
        return

    color = {
        "allow": "green",
        "deny": "red",
        "require_approval": "yellow",
    }.get(result["decision"], "white")
    console.print(f"[{color}]decision: {result['decision']}[/{color}]")
    if result["rule"]:
        console.print(f"rule: {result['rule']}")
    if result["reason"]:
        console.print(f"reason: {result['reason']}")
    matched = result["matched_capability"]
    if matched:
        console.print(f"matched capability: {matched['kind']}({matched['pattern']})")
