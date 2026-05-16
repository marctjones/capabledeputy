"""Tests for the shared presentation module (REPL + TUI single source
of truth). Pure functions over plain dicts — fully unit-testable."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from capabledeputy.presentation import (
    DENY_RECOVERY,
    capability_line,
    capability_markers,
    capability_summary_rich,
    compartment_summary,
    label_style,
    render_labels,
)

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def test_label_palette() -> None:
    assert label_style("untrusted.external") == "bold red"
    assert label_style("confidential.health") == "yellow"
    assert label_style("trusted.user_direct") == "green"
    assert label_style("egress.email") == "magenta"
    assert label_style("other.thing") == "white"


def test_render_labels_empty_and_sorted() -> None:
    assert render_labels([]) == "[green]clean[/green]"
    out = render_labels(["untrusted.external", "confidential.health"])
    assert out.index("confidential.health") < out.index("untrusted.external")


def test_compartment_precedence() -> None:
    assert compartment_summary([]) == ("clean", "green")
    assert compartment_summary(["trusted.user_direct"]) == ("clean", "green")
    assert compartment_summary(["confidential.financial"]) == (
        "confidential",
        "yellow",
    )
    assert compartment_summary(
        ["confidential.financial", "untrusted.external"],
    ) == ("TAINTED", "bold red")


def test_capability_markers_unconstrained_is_empty() -> None:
    assert capability_markers({"kind": "READ_FS", "pattern": "*"}) == []
    assert capability_summary_rich({"kind": "READ_FS", "pattern": "*"}) == ""


def test_capability_markers_full_family_stable_order() -> None:
    cap = {
        "kind": "QUEUE_PURCHASE",
        "pattern": "amazon",
        "expiry": "one_shot",
        "allows_destructive": True,
        "max_amount": 100,
        "expires_at": (_NOW + timedelta(seconds=30)).isoformat(),
        "rate_limit": {"max_uses": 3, "window_seconds": 60},
        "revoked_by": ["WEB_FETCH"],
    }
    assert capability_markers(cap, now=_NOW) == [
        "one-shot",
        "destructive",
        "max=100",
        "expires 30s",
        "rate 3/60s",
        "revoked-by:WEB_FETCH",
    ]


def test_capability_markers_expired_vs_future() -> None:
    past = {
        "kind": "READ_FS", "pattern": "*",
        "expires_at": (_NOW - timedelta(seconds=1)).isoformat(),
    }
    future = {
        "kind": "READ_FS", "pattern": "*",
        "expires_at": (_NOW + timedelta(seconds=90)).isoformat(),
    }
    assert capability_markers(past, now=_NOW) == ["expired"]
    assert capability_markers(future, now=_NOW) == ["expires 90s"]


def test_capability_summary_rich_colours_unusable_red() -> None:
    cap = {
        "kind": "READ_FS", "pattern": "*",
        "expires_at": (_NOW - timedelta(seconds=1)).isoformat(),
    }
    out = capability_summary_rich(cap, now=_NOW)
    assert "[red]expired[/red]" in out


def test_capability_line_shape() -> None:
    line = capability_line(
        {"kind": "SEND_EMAIL", "pattern": "*@x.com"}, now=_NOW,
    )
    assert "[bold]SEND_EMAIL[/bold]" in line
    assert "pattern=*@x.com" in line


def test_deny_recovery_covers_all_v07_rules() -> None:
    for rule in (
        "untrusted-meets-egress",
        "health-meets-egress",
        "financial-meets-email",
        "capability-revoked-by-prior-use",
        "capability-expired",
        "rate-limit-exceeded",
    ):
        assert DENY_RECOVERY.get(rule)
