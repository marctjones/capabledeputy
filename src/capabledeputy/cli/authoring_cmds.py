"""#387 — mutation commands: routine policy changes without hand-editing YAML.

`docs/policy-authoring-design.md` §8: `capdep posture use`, `capdep rule add`,
`capdep label add` edit the unified `capdep.yaml` **write-through** — every
mutation is compiled + validated (`check_policy`, the #385 gate) BEFORE the file
is written, so an invalid change refuses without touching the file. Hand-editing
YAML stays the advanced path; these are the routine one-liners.

The write-through core (`mutate_document`) is a pure function of
(current-doc, mutation) → (new-doc | error), so it is unit-testable without the
CLI; the Typer commands are thin wrappers.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer
import yaml
from rich.console import Console

from capabledeputy.policy.authoring import (
    ConfigError,
    apply_defaults,
    compile_document,
)
from capabledeputy.policy.policy_check import PolicyProblem, check_policy, has_errors

console = Console()
err_console = Console(stderr=True)

DEFAULT_DOC = Path("configs/capdep.yaml")


class MutationRefusedError(RuntimeError):
    """A mutation was refused because the resulting policy fails validation.
    Carries the problems so callers can render them; the file is never written."""

    def __init__(self, problems: list[PolicyProblem]) -> None:
        self.problems = problems
        super().__init__(f"{sum(p.severity == 'error' for p in problems)} error(s)")


def load_document(path: Path) -> dict:
    """Load the unified document as a plain dict (empty when the file is absent).
    Fail-closed (ConfigError) on a non-mapping root or unparseable YAML."""
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"{path} unparseable: {e}") from e
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: policy document root must be a mapping")
    return data


def validate_document(doc: dict) -> list[PolicyProblem]:
    """Compile + cross-reference-check a candidate document exactly as the daemon
    would load it (defaults applied). Raises ConfigError on a compile failure;
    returns problems (possibly empty) otherwise."""
    compiled = apply_defaults(compile_document(doc))
    return check_policy(compiled)


def mutate_document(current: dict, mutate: Callable[[dict], None]) -> dict:
    """Apply `mutate` to a COPY of `current`, validate the result, and return the
    new document. Raises `MutationRefusedError` (leaving the input untouched) if the
    result has any error-severity problem, or `ConfigError` if it won't compile.
    The pure write-through core."""
    import copy

    candidate = copy.deepcopy(current)
    mutate(candidate)
    problems = validate_document(candidate)
    if has_errors(problems):
        raise MutationRefusedError(problems)
    return candidate


def _write(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def _apply_and_write(path: Path, mutate: Callable[[dict], None], ok_message: str) -> None:
    """CLI helper: load → mutate+validate → write, or refuse with exit code."""
    try:
        current = load_document(path)
        new_doc = mutate_document(current, mutate)
    except ConfigError as e:
        err_console.print(f"[red]config error[/red] {e}")
        raise typer.Exit(2) from None
    except MutationRefusedError as e:
        err_console.print("[red]change refused[/red] — the result would be invalid:")
        for p in e.problems:
            if p.severity == "error":
                err_console.print(f"  [red]error[/red] {p.where}: {p.message}")
        raise typer.Exit(1) from None
    _write(path, new_doc)
    console.print(f"[green]{ok_message}[/green] → {path}")


# --- capdep posture use <id> ----------------------------------------------

posture_app = typer.Typer(help="Select the active security posture.", no_args_is_help=True)


@posture_app.callback()
def _posture_root() -> None:
    """Select the active security posture (keeps `use` as a named subcommand)."""


@posture_app.command("use")
def posture_use(
    posture_id: Annotated[
        str,
        typer.Argument(help="Preset id: strict / high-security-useful / low-friction-practical."),
    ],
    path: Annotated[Path, typer.Option("--file", help="Unified policy document.")] = DEFAULT_DOC,
) -> None:
    """Set the active posture to a shipped preset (write-through + validated)."""

    def _mutate(doc: dict) -> None:
        doc["posture"] = {"use": posture_id}

    _apply_and_write(path, _mutate, f"posture set to {posture_id!r}")


# --- capdep rule add <id> "<when> -> <then>" -------------------------------

rule_app = typer.Typer(help="Add decision rules.", no_args_is_help=True)


@rule_app.callback()
def _rule_root() -> None:
    """Add decision rules (keeps `add` as a named subcommand)."""


@rule_app.command("add")
def rule_add(
    rule_id: Annotated[str, typer.Argument(help="Unique rule id.")],
    spec: Annotated[
        str, typer.Argument(help='"<when> -> <then>", e.g. "financial + send_email -> deny".')
    ],
    because: Annotated[str, typer.Option("--because", help="Human rationale.")] = "",
    path: Annotated[Path, typer.Option("--file", help="Unified policy document.")] = DEFAULT_DOC,
) -> None:
    """Append a decision rule authored in the compact grammar (write-through)."""
    sep = "→" if "→" in spec else "->"
    if sep not in spec:
        err_console.print("[red]bad rule[/red] — expected '<when> -> <then>'")
        raise typer.Exit(2)
    when, _, then = spec.partition(sep)
    when, then = when.strip(), then.strip()

    def _mutate(doc: dict) -> None:
        rules = doc.setdefault("rules", [])
        if any(isinstance(r, dict) and r.get("id") == rule_id for r in rules):
            raise MutationRefusedError(
                [PolicyProblem(f"rule {rule_id!r}", "id already exists", "error")],
            )
        entry: dict = {"id": rule_id, "when": when, "then": then}
        if because:
            entry["because"] = because
        rules.append(entry)

    _apply_and_write(path, _mutate, f"rule {rule_id!r} added")


# --- capdep label add <category> --------------------------------------------

label_app = typer.Typer(help="Declare Axis-A categories.", no_args_is_help=True)


@label_app.callback()
def _label_root() -> None:
    """Declare Axis-A categories (keeps `add` as a named subcommand)."""


@label_app.command("add")
def label_add(
    category: Annotated[str, typer.Argument(help="Category id, e.g. financial.")],
    tier: Annotated[
        str, typer.Option("--tier", help="none/sensitive/regulated/restricted/prohibited.")
    ] = "sensitive",
    path: Annotated[Path, typer.Option("--file", help="Unified policy document.")] = DEFAULT_DOC,
) -> None:
    """Declare a new Axis-A category in the label catalog (write-through).

    (Binding a specific source/address to a label — `label bind` — awaits the
    source-matching label-rule sub-grammar; this adds the category definition.)"""

    def _mutate(doc: dict) -> None:
        labels = doc.setdefault("labels", [])
        if any(isinstance(x, dict) and x.get("category") == category for x in labels):
            raise MutationRefusedError(
                [PolicyProblem(f"category {category!r}", "already declared", "error")],
            )
        labels.append({"category": category, "tier": tier})

    _apply_and_write(path, _mutate, f"category {category!r} added at tier {tier!r}")
