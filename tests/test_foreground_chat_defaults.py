"""Foreground chat sessions should be born with usable tool capabilities."""

from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.policy.capabilities import Capability, CapabilityKind, kind_name
from capabledeputy.policy.purposes import load as load_purposes
from capabledeputy.session.foreground_defaults import (
    foreground_chat_default_capabilities,
    should_apply_foreground_defaults,
    supplement_foreground_capabilities,
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
        cap.pattern for cap in general.default_capabilities if kind_name(cap.kind) == "WEB_FETCH"
    ]
    assert "*" in web_caps


def test_personal_assistant_web_fetch_allows_search_targets() -> None:
    purposes = load_purposes(Path("configs/personal-assistant/purposes.yaml"))
    for purpose_id in ("general", "writing", "research"):
        purpose = purposes.get(purpose_id)
        assert purpose is not None
        assert any(
            kind_name(cap.kind) == "WEB_FETCH" and cap.pattern == "*"
            for cap in purpose.default_capabilities
        )


@pytest.mark.parametrize(
    "purposes_path",
    ["configs/purposes.yaml", "configs/personal-assistant/purposes.yaml"],
)
def test_general_purpose_grants_memory_read_create_write_but_not_modify_or_delete(
    purposes_path: str,
) -> None:
    """Memory keys are arbitrary strings, not filesystem paths — a
    "general" session must be able to read/create/write memory by
    default without that also widening filesystem authority (the old
    CREATE_FS/READ_FS/WRITE_FS overload made that impossible).
    MEMORY_WRITE mirrors WRITE_FS (blind upsert, non-destructive);
    MEMORY_MODIFY (modify-existing-only) and MEMORY_DELETE stay
    ungranted by default, same as the other destructive kinds."""
    purposes = load_purposes(Path(purposes_path))
    general = purposes.get("general")
    assert general is not None
    granted = {kind_name(cap.kind): cap.pattern for cap in general.default_capabilities}
    assert granted.get("MEMORY_READ") == "*"
    assert granted.get("MEMORY_CREATE") == "*"
    assert granted.get("MEMORY_WRITE") == "*"
    assert "MEMORY_MODIFY" not in granted
    assert "MEMORY_DELETE" not in granted


def test_should_apply_foreground_defaults_for_gui_owner() -> None:
    assert should_apply_foreground_defaults(
        owner="CapDepMac",
        purpose_handle="general",
        capability_count=0,
    )
    caps = foreground_chat_default_capabilities()
    assert should_apply_foreground_defaults(
        owner="CapDepMac",
        purpose_handle="general",
        capability_count=3,
        capability_set=frozenset(caps[:3]),
    )
    assert not should_apply_foreground_defaults(
        owner="CapDepMac",
        purpose_handle="general",
        capability_count=len(caps),
        capability_set=frozenset(caps),
    )


def test_supplement_foreground_capabilities_adds_missing_image_caps() -> None:
    caps = foreground_chat_default_capabilities()
    without_image = frozenset(
        cap
        for cap in caps
        if cap.kind not in {CapabilityKind.GENERATE_IMAGE, CapabilityKind.FETCH_IMAGE}
    )
    added = supplement_foreground_capabilities(without_image)
    kinds = {cap.kind for cap in added}
    assert CapabilityKind.GENERATE_IMAGE in kinds
    assert CapabilityKind.FETCH_IMAGE in kinds


def test_supplement_foreground_capabilities_adds_missing_web_search_wildcard() -> None:
    caps = frozenset(
        {
            Capability(kind=CapabilityKind.WEB_FETCH, pattern="https://*"),
            Capability(kind=CapabilityKind.GENERATE_IMAGE, pattern="*"),
            Capability(kind=CapabilityKind.FETCH_IMAGE, pattern="*"),
        },
    )

    added = supplement_foreground_capabilities(caps)

    assert any(cap.kind is CapabilityKind.WEB_FETCH and cap.pattern == "*" for cap in added)


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
