"""Migration tail — `capdep policy why`: offline what-if decision explainer."""

from __future__ import annotations

from typer.testing import CliRunner

from capabledeputy.cli.policy import policy_app

runner = CliRunner()


def test_why_untrusted_email_is_denied_at_floor() -> None:
    result = runner.invoke(policy_app, ["why", "SEND_EMAIL", "--to", "bob@x.com", "--untrusted"])
    assert result.exit_code == 0
    assert "DENY" in result.stdout
    assert "untrusted-meets-egress" in result.stdout
    assert "floor" in result.stdout


def test_why_health_email_is_denied() -> None:
    result = runner.invoke(
        policy_app, ["why", "SEND_EMAIL", "--to", "doc@x.com", "--category", "health"]
    )
    assert result.exit_code == 0
    assert "DENY" in result.stdout


def test_why_clean_read_is_allowed() -> None:
    result = runner.invoke(policy_app, ["why", "READ_FS", "--to", "/tmp/n.txt"])
    assert result.exit_code == 0
    assert "ALLOW" in result.stdout


def test_why_unknown_kind_exits_2() -> None:
    result = runner.invoke(policy_app, ["why", "TELEPORT"])
    assert result.exit_code == 2
    assert "unknown kind" in result.stdout
