"""Tests for the REPL presentation helpers.

These are pure functions (palette, compartment summary, toolbar
rendering) — the interactive loop itself is exercised by hand. The
toolbar render is tested against a fake CompletionCache so we can
assert the color-coded compartment band without a daemon.
"""

from __future__ import annotations

from typing import Any

from prompt_toolkit.formatted_text import to_plain_text

from capabledeputy.cli.chat import (
    _DENY_RECOVERY,
    _compartment_summary,
    _label_rich_style,
    _make_bottom_toolbar,
    _render_labels_rich,
    _render_outcomes_table,
    console,
)


def test_label_style_palette() -> None:
    assert _label_rich_style("untrusted.external") == "bold red"
    assert _label_rich_style("confidential.health") == "yellow"
    assert _label_rich_style("trusted.user_direct") == "green"
    assert _label_rich_style("egress.email") == "magenta"
    assert _label_rich_style("something.else") == "white"


def test_render_labels_empty_is_clean() -> None:
    assert _render_labels_rich([]) == "[green]clean[/green]"


def test_render_labels_sorted_and_styled() -> None:
    out = _render_labels_rich(["untrusted.external", "confidential.health"])
    # sorted: confidential first
    assert out.index("confidential.health") < out.index("untrusted.external")
    assert "[bold red]untrusted.external[/bold red]" in out


def test_compartment_summary_precedence() -> None:
    assert _compartment_summary([]) == ("clean", "green")
    assert _compartment_summary(["trusted.user_direct"]) == ("clean", "green")
    assert _compartment_summary(["confidential.financial"]) == (
        "confidential",
        "yellow",
    )
    # untrusted dominates even with confidential present
    assert _compartment_summary(
        ["confidential.financial", "untrusted.external"],
    ) == ("TAINTED", "bold red")


def test_deny_recovery_covers_the_hard_deny_rules() -> None:
    for rule in (
        "untrusted-meets-egress",
        "health-meets-egress",
        "financial-meets-email",
        "capability-revoked-by-prior-use",
    ):
        assert rule in _DENY_RECOVERY
        assert _DENY_RECOVERY[rule]


class _FakeCache:
    def __init__(self, sessions: list[dict[str, Any]], approval_ids: list[int]):
        self.sessions = sessions
        self.approval_ids = approval_ids


def _toolbar_text(cache: Any, sid: str) -> str:
    focus = {"id": sid, "label": sid[:8]}
    render = _make_bottom_toolbar(cache, focus)
    return to_plain_text(render())


def test_toolbar_syncing_when_session_not_in_cache() -> None:
    txt = _toolbar_text(_FakeCache([], []), "abcd1234-0000-0000-0000-x")
    assert "abcd1234" in txt
    assert "syncing" in txt


def test_toolbar_clean_session() -> None:
    sid = "abcd1234-0000-0000-0000-000000000001"
    cache = _FakeCache(
        [{"id": sid, "label_set": [], "capability_set": [1, 2, 3]}],
        [],
    )
    txt = _toolbar_text(cache, sid)
    assert "clean" in txt
    assert "caps 3" in txt
    assert "pending" not in txt


def test_toolbar_tainted_with_pending() -> None:
    sid = "abcd1234-0000-0000-0000-000000000002"
    cache = _FakeCache(
        [
            {
                "id": sid,
                "label_set": ["untrusted.external", "confidential.personal"],
                "capability_set": [1],
            },
        ],
        [7, 9],
    )
    txt = _toolbar_text(cache, sid)
    assert "TAINTED" in txt
    assert "untrusted.external" in txt
    assert "2 pending" in txt


def _capture_outcomes(outcomes: list[dict[str, Any]]) -> str:
    with console.capture() as cap:
        _render_outcomes_table(outcomes)
    return cap.get()


def test_real_deny_shows_recovery_hint() -> None:
    out = _capture_outcomes(
        [
            {
                "decision": "deny",
                "tool_name": "email.send",
                "rule": "untrusted-meets-egress",
                "reason": "rule fired on labels",
            },
        ],
    )
    assert "recover" in out
    assert "/spawn" in out or "/extract" in out


def test_preview_deny_surfaces_hint_even_without_deny_row() -> None:
    """The agent preview-checks and skips the real call, so the only
    outcome is `allow policy.preview` whose OUTPUT says deny. The
    recovery hint must still appear."""
    out = _capture_outcomes(
        [
            {
                "decision": "allow",
                "tool_name": "policy.preview",
                "output": {
                    "decision": "deny",
                    "rule": "untrusted-meets-egress",
                    "reason": "would fire",
                },
            },
        ],
    )
    assert "preview" in out
    assert "would DENY" in out
    assert "recover" in out


def test_preview_allow_shows_no_hint() -> None:
    out = _capture_outcomes(
        [
            {
                "decision": "allow",
                "tool_name": "policy.preview",
                "output": {"decision": "allow", "rule": None},
            },
        ],
    )
    assert "recover" not in out
    assert "would DENY" not in out


# --- Time-bounded capability rendering (feature 001, US3) ----------------

from datetime import UTC, datetime, timedelta  # noqa: E402

from capabledeputy.cli.chat import _expiry_marker  # noqa: E402


def _cap(expires_at: str | None) -> dict[str, Any]:
    return {"kind": "READ_FS", "pattern": "*", "expires_at": expires_at}


def test_expiry_marker_none_is_empty() -> None:
    assert _expiry_marker(_cap(None)) == ""


def test_expiry_marker_future_shows_remaining() -> None:
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    deadline = (now + timedelta(seconds=90)).isoformat()
    out = _expiry_marker(_cap(deadline), now=now)
    assert "expires in" in out
    assert "90s" in out


def test_expiry_marker_past_shows_expired() -> None:
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    deadline = (now - timedelta(seconds=1)).isoformat()
    out = _expiry_marker(_cap(deadline), now=now)
    assert "expired" in out


def test_status_render_annotates_time_bounded_cap(
    monkeypatch: Any,
) -> None:
    from capabledeputy.cli import chat

    future = (datetime.now(UTC) + timedelta(seconds=120)).isoformat()
    monkeypatch.setattr(
        chat,
        "_call",
        lambda m, p=None: {
            "label_set": [],
            "used_kinds": [],
            "capability_set": [
                {"kind": "READ_FS", "pattern": "*", "expires_at": future},
                {"kind": "SEND_EMAIL", "pattern": "*", "expires_at": None},
            ],
        },
    )
    with chat.console.capture() as cap:
        chat._handle_status("sid", only="caps")
    out = cap.get()
    assert "expires in" in out  # the time-bounded one annotated
    # the non-expiring one has no marker on its line
    assert "SEND_EMAIL pattern=*" in out


def test_session_show_annotates_time_bounded_cap(monkeypatch: Any) -> None:
    from capabledeputy.cli import chat

    past = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
    monkeypatch.setattr(
        chat,
        "_call",
        lambda m, p=None: {
            "id": "abcd1234-...",
            "status": "active",
            "capability_set": [
                {"kind": "READ_FS", "pattern": "*", "expires_at": past},
            ],
        },
    )
    with chat.console.capture() as cap:
        chat._handle_session_show("abcd1234")
    assert "expired" in cap.get()


def test_toolbar_shows_time_bound_marker() -> None:
    sid = "abcd1234-0000-0000-0000-000000000099"
    future = (datetime.now(UTC) + timedelta(seconds=300)).isoformat()
    cache = _FakeCache(
        [
            {
                "id": sid,
                "label_set": [],
                "capability_set": [
                    {"kind": "READ_FS", "pattern": "*", "expires_at": future},
                    {"kind": "SEND_EMAIL", "pattern": "*", "expires_at": None},
                ],
            },
        ],
        [],
    )
    txt = _toolbar_text(cache, sid)
    # one of two caps is time-bounded → toolbar surfaces it
    assert "ttl" in txt or "⏳" in txt
