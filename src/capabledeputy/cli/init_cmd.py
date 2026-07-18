"""`capdep init` — first-run onboarding wizard.

Seven interactive prompts, then writes config under XDG paths:

  $XDG_CONFIG_HOME/capabledeputy/    or  ~/.config/capabledeputy/
    config.yaml              (top-level config — risk dial, profile, etc.)
    secrets/anthropic.key    (optional API key file, mode 0o600)
    policies/                (empty dir for operator-authored primitives)

Designed for a non-engineer operator to go from a fresh install to a
working `capdep chat` in under 10 minutes (SC-200 from spec 005).

Conventions:
  - Default answers are recommended; the wizard pre-selects them
  - The wizard refuses to overwrite an existing config unless --force
  - Social-commitment tools (email.send, purchase.queue, etc.) default
    to disabled — opt-in only
  - The wizard does NOT start the daemon or create a session; it
    just writes config and tells the operator the next commands
"""

from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table

console = Console()
err_console = Console(stderr=True)


def _config_home() -> Path:
    """XDG-style config directory for CapableDeputy."""
    override = os.environ.get("XDG_CONFIG_HOME")
    base = Path(override) if override else Path.home() / ".config"
    return base / "capabledeputy"


def _sandbox_readiness_line(command_runner: Any = None) -> str:
    """One-line first-run note about Pattern 5 (SEALED) reachability (#361).

    `capdep init` does not itself write the daemon sandbox block (it writes only
    the XDG config, and never touches daemon.yaml), so it *surfaces* readiness
    and the exact one command that makes SEALED reachable — so a fresh install
    is one obvious step away, not silently missing. Probing is best-effort and
    must never break init, hence the broad guard."""
    try:
        from capabledeputy.cli._managed_config import podman_readiness

        readiness, _ = podman_readiness(command_runner)
    except Exception:
        readiness = "not_installed"
    if readiness == "ready":
        return (
            "[green]Sealed sandbox (Pattern 5) available[/green] — run "
            "[bold]capdep-setup sandbox --apply[/bold] to enable SEALED, "
            "egress-free execution for restricted-tier work."
        )
    if readiness == "machine_not_running":
        return (
            "[yellow]Podman is installed but its machine is not running[/yellow] — "
            "[bold]podman machine start[/bold], then "
            "[bold]capdep-setup sandbox --apply[/bold] to enable SEALED (Pattern 5)."
        )
    return (
        "[dim]Sealed sandbox (Pattern 5) is optional: install Podman "
        "([bold]brew install podman[/bold]) then [bold]capdep-setup sandbox --apply[/bold]. "
        "Without it, restricted-tier work still runs via Pattern 3 handle-routing.[/dim]"
    )


def _detect_os() -> str:
    """Friendly OS name for the wizard banner."""
    system = platform.system()
    if system == "Linux":
        try:
            import shutil

            if shutil.which("wsl.exe") or os.environ.get("WSL_DISTRO_NAME"):
                return "WSL2 (Linux on Windows)"
        except Exception:
            pass
        return "Linux"
    if system == "Darwin":
        return f"macOS {platform.mac_ver()[0]}"
    if system == "Windows":
        return "Windows (native; recommend WSL2)"
    return system or "unknown"


def _prompt_choice(label: str, options: list[tuple[str, str]], default: int = 1) -> int:
    """Ask the operator to pick a numbered option. Returns 1-based index."""
    table = Table(show_header=False, padding=(0, 1), box=None)
    for i, (name, desc) in enumerate(options, start=1):
        marker = " [dim](recommended)[/dim]" if i == default else ""
        table.add_row(f"  [bold]{i}[/bold]", f"{name}{marker}", f"[dim]{desc}[/dim]")
    console.print(table)
    return IntPrompt.ask(
        f"  {label}",
        default=default,
        choices=[str(i) for i in range(1, len(options) + 1)],
        show_choices=False,
    )


def _write_anthropic_key_file(key: str, config_dir: Path) -> Path:
    """Save the API key to a 0o600 file under the config dir."""
    secrets_dir = config_dir / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    key_path = secrets_dir / "anthropic.key"
    key_path.write_text(key.strip() + "\n", encoding="utf-8")
    key_path.chmod(0o600)
    return key_path


def _write_config(
    config_dir: Path,
    *,
    llm_provider: str,
    anthropic_key_path: Path | None,
    profile: str,
    risk_preference: str,
    enable_social_tools: bool,
) -> Path:
    """Write the top-level config.yaml. Minimal — most policy lives
    in separate files (bindings.yaml, envelopes.yaml, etc.) that the
    operator can add later. The wizard's goal is "enough to run."
    """
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "policies").mkdir(exist_ok=True)
    config_path = config_dir / "config.yaml"

    lines = [
        "# CapableDeputy configuration — generated by `capdep init`.",
        "# Edit by hand or re-run `capdep init --force` to regenerate.",
        "",
        "llm:",
        f"  provider: {llm_provider}",
    ]
    if anthropic_key_path is not None:
        lines.append(f"  anthropic_key_path: {anthropic_key_path}")
    elif llm_provider == "anthropic":
        lines.append("  # ANTHROPIC_API_KEY must be set in your shell env")

    lines.extend(
        [
            "",
            "operator:",
            f"  default_profile: {profile}",
            f"  risk_preference: {risk_preference}",
            "",
            "tools:",
            f"  enable_social_commitment: {str(enable_social_tools).lower()}",
            "  # When false, email.send / purchase.queue / etc. are not registered",
            "  # at daemon startup. Override per-session via /grant if needed.",
            "",
            "# Add operator-authored policies under ./policies/",
            "# Add upstream MCP servers under ./upstream_servers/",
            "",
        ],
    )
    config_path.write_text("\n".join(lines), encoding="utf-8")
    return config_path


def _write_assistant_surface(command_runner: Any = None) -> tuple[Path, list[str]]:
    """#327 — wire the curated SAFE default assistant surface (fs read + memory +
    web search + fetch + chart/native, sandbox iff Podman is ready) into the
    user-default daemon config, so a fresh install is useful on day one WITHOUT
    an extra `capdep-setup assistant-surface --apply`. Idempotent (managed
    blocks); returns the daemon-config path plus per-block status messages."""
    from capabledeputy.cli._managed_config import (
        register_default_assistant_surface,
        user_default_daemon_config_path,
    )

    path = user_default_daemon_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # include_sandbox=None → auto: written iff Podman is ready (deep check).
    messages = register_default_assistant_surface(path)
    return path, messages


def init_command(
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Overwrite existing config without prompting",
        ),
    ] = False,
    non_interactive: Annotated[
        bool,
        typer.Option(
            "--non-interactive",
            help="Skip prompts; write defaults. Useful for CI / scripted installs.",
        ),
    ] = False,
    no_assistant_surface: Annotated[
        bool,
        typer.Option(
            "--no-assistant-surface",
            help="Don't auto-wire the bundled safe assistant surface (#327).",
        ),
    ] = False,
) -> None:
    """First-run onboarding wizard.

    Seven prompts. Writes config under $XDG_CONFIG_HOME/capabledeputy/, and
    (unless --no-assistant-surface) wires the curated safe default assistant
    surface so `capdep chat` is useful immediately.
    """
    config_dir = _config_home()
    config_path = config_dir / "config.yaml"

    console.print(
        Panel(
            (
                "[bold]Welcome to CapableDeputy.[/bold]\n\n"
                "Seven questions; then you'll be ready to run "
                "[bold]capdep daemon start[/bold].\n"
                "Defaults are pre-selected (in [dim]recommended[/dim])."
            ),
            border_style="green",
        ),
    )

    if config_path.exists() and not force:
        err_console.print(
            f"[yellow]config already exists at {config_path}[/yellow]\n"
            "  Re-run with [bold]--force[/bold] to overwrite, or edit by hand.",
        )
        raise typer.Exit(code=2)

    # 1. OS detection (display only)
    os_name = _detect_os()
    console.print(f"\n[bold][1/7][/bold] Operating system: [green]{os_name}[/green]")
    console.print(f"        Config dir: [dim]{config_dir}[/dim]")

    if non_interactive:
        # Skip everything; write defaults.
        provider = "anthropic"
        key_path = None
        profile = "unrestricted"
        risk_preference = "balanced"
        enable_social_tools = False
    else:
        # 2. LLM provider
        console.print("\n[bold][2/7][/bold] Which LLM provider?")
        provider_idx = _prompt_choice(
            "choice",
            [
                ("Anthropic Claude", "via LiteLLM; requires API key (recommended)"),
                ("Ollama", "local; requires ollama installed"),
                ("Claude Code CLI", "uses your Claude Code login; slower"),
                ("None for now", "set up later"),
            ],
            default=1,
        )
        provider = ["anthropic", "ollama", "claude_code", "none"][provider_idx - 1]

        # 3. Anthropic API key — only if Anthropic chosen
        key_path: Path | None = None
        if provider == "anthropic":
            console.print("\n[bold][3/7][/bold] Where is your Anthropic API key?")
            key_loc = _prompt_choice(
                "choice",
                [
                    (
                        "Environment variable ANTHROPIC_API_KEY",
                        "I'll export it in my shell (recommended)",
                    ),
                    (
                        "Save to file",
                        "~/.config/capabledeputy/secrets/anthropic.key (mode 0600)",
                    ),
                    ("Skip for now", "set up later; chat won't work until provided"),
                ],
                default=1,
            )
            if key_loc == 2:
                key = Prompt.ask("  API key", password=True)
                if key.strip():
                    key_path = _write_anthropic_key_file(key, config_dir)
                    console.print(f"  [green]saved[/green] to {key_path}")
                else:
                    console.print("  [yellow]empty input; skipping[/yellow]")
        else:
            console.print(
                f"\n[bold][3/7][/bold] [dim]skipped (provider is {provider})[/dim]",
            )

        # 4. Default operator profile
        console.print("\n[bold][4/7][/bold] Default operator profile (clearance ceiling):")
        profile_idx = _prompt_choice(
            "choice",
            [
                ("unrestricted", "no read-up ceiling; for personal use"),
                ("auditor", "max_tier=RESTRICTED; can read regulated/restricted"),
                ("standard", "max_tier=SENSITIVE; common work scenarios"),
                ("intern", "max_tier=NONE; only public-tier reads"),
            ],
            default=1,
        )
        profile = ["unrestricted", "auditor", "standard", "intern"][profile_idx - 1]

        # 5. Risk preference dial
        console.print("\n[bold][5/7][/bold] Risk-preference dial:")
        risk_idx = _prompt_choice(
            "choice",
            [
                ("cautious", "every gate prompts for approval"),
                ("balanced", "auto-approve safe ops; prompt on risk (recommended)"),
                ("permissive", "auto-approve unless hard floor crosses"),
            ],
            default=2,
        )
        risk_preference = ["cautious", "balanced", "permissive"][risk_idx - 1]

        # 6. Social-commitment tools
        console.print(
            "\n[bold][6/7][/bold] Enable social-commitment tools (email.send, purchase.queue)?",
        )
        social_idx = _prompt_choice(
            "choice",
            [
                ("No", "default; safer — opt in per session via /grant"),
                ("Yes", "register at daemon startup; gated by chokepoint"),
            ],
            default=1,
        )
        enable_social_tools = social_idx == 2

        # 7. Review
        console.print("\n[bold][7/7][/bold] Review:")
        review = Table(show_header=False, box=None, padding=(0, 1))
        review.add_row("  LLM provider:", f"[green]{provider}[/green]")
        if key_path:
            review.add_row("  API key file:", str(key_path))
        review.add_row("  Default profile:", profile)
        review.add_row("  Risk dial:", risk_preference)
        review.add_row(
            "  Social-commitment tools:",
            "[green]enabled[/green]" if enable_social_tools else "[dim]disabled[/dim]",
        )
        review.add_row("  Config dir:", str(config_dir))
        console.print(review)

        if not Confirm.ask("\n  Write these?", default=True):
            console.print("[yellow]cancelled; nothing written.[/yellow]")
            raise typer.Exit(code=2)

    written = _write_config(
        config_dir,
        llm_provider=provider,
        anthropic_key_path=key_path,
        profile=profile,
        risk_preference=risk_preference,
        enable_social_tools=enable_social_tools,
    )

    # #327 — wire the safe default assistant surface so day one is useful.
    surface_path: Path | None = None
    surface_messages: list[str] = []
    if not no_assistant_surface:
        surface_path, surface_messages = _write_assistant_surface()

    surface_block = ""
    if surface_path is not None:
        surface_block = (
            f"\n\n[bold]Assistant surface[/bold] (wired → {surface_path}):\n"
            + "\n".join(f"  [dim]· {m}[/dim]" for m in surface_messages)
        )

    console.print(
        Panel(
            (
                "[green]wrote[/green]\n"
                f"  {written}\n"
                f"  {config_dir}/policies/  (empty)"
                + (f"\n  {key_path}" if key_path else "")
                + surface_block
                + "\n\n"
                "[bold]Next steps:[/bold]\n"
                + (
                    "  1. [bold]export ANTHROPIC_API_KEY=...[/bold]"
                    "  (in your shell rc, if not done already)\n"
                    if provider == "anthropic" and key_path is None
                    else ""
                )
                + "  1. [bold]capdep daemon start[/bold]\n"
                "  2. [bold]capdep session create[/bold]\n"
                "  3. [bold]capdep chat[/bold]\n\n" + _sandbox_readiness_line() + "\n\n"
                "[dim]To regenerate this config, re-run [bold]capdep init --force[/bold].[/dim]"
            ),
            title="Done",
            border_style="green",
        ),
    )
