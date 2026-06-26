"""Foreground chat sessions should be born with usable tool capabilities."""

from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.policy.purposes import load as load_purposes
from capabledeputy.session.foreground_defaults import (
    foreground_chat_default_capabilities,
    should_apply_foreground_defaults,
)
from capabledeputy.session.graph import SessionGraph


def test_general_purpose_in_repo_configs_has_default_capabilities() -> None:
    purposes = load_purposes(Path("configs/purposes.yaml"))
    general = purposes.get("general")
    assert general is not None
    assert len(general.default_capabilities) >= 10


def test_general_purpose_web_fetch_allows_search_targets() -> None:
    """WEB_FETCH * matches search queries; https://* does not."""
    purposes = load_purposes(Path("configs/purposes.yaml"))
    general = purposes.get("general")
    assert general is not None
    web_caps = [
        cap.pattern
        for cap in general.default_capabilities
        if cap.kind.value == "WEB_FETCH"
    ]
    assert "*" in web_caps


def test_should_apply_foreground_defaults_for_gui_owner() -> None:
    assert should_apply_foreground_defaults(
        owner="CapDepMac",
        purpose_handle="general",
        capability_count=0,
    )
    assert not should_apply_foreground_defaults(
        owner="CapDepMac",
        purpose_handle="general",
        capability_count=3,
    )


@pytest.mark.asyncio
async def test_session_new_applies_foreground_defaults_when_empty() -> None:
    graph = SessionGraph()
    session = await graph.new(
        owner="CapDepMac",
        intent="chat probe",
        purpose_handle="unset",
    )
    assert session.capability_set == frozenset()

    from capabledeputy.daemon.session_handlers import make_session_handlers

    handlers = make_session_handlers(graph)
    created = await handlers["session.new"](
        {
            "owner": "CapDepMac",
            "intent": "chat probe",
            "purpose_handle": "unset",
        },
    )
    assert len(created.get("capability_set") or []) == len(
        foreground_chat_default_capabilities(),
    )