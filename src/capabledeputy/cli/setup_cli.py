"""Top-level setup automation entry point for CapDep."""

from __future__ import annotations

import typer

from capabledeputy.cli.google_cloud_setup import app as google_cloud_app

app = typer.Typer(
    help=(
        "One-time CapDep setup automation. These commands prepare external "
        "accounts, machine-local assets, and optional integrations without "
        "adding configuration workflows to the main capdep command."
    ),
    no_args_is_help=True,
)

app.add_typer(
    google_cloud_app,
    name="google-cloud",
    help="Prepare Google Cloud and Workspace API access for CapDep OAuth.",
)


@app.command("list")
def list_setups() -> None:
    """List available setup automation domains."""
    typer.echo("google-cloud\tGoogle Cloud / Workspace OAuth API enablement")


if __name__ == "__main__":
    app()
