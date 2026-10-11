"""Real mail selection is usability, not a permission grant (#461)."""

import pytest

from capabledeputy.agent.chat_turn import is_conversational_turn
from capabledeputy.agent.tool_selection import mail_tool_surface, select_tools_for_turn
from capabledeputy.llm.models_config import ToolSelectionConfig
from capabledeputy.mode.dispatcher import ExecutionMode
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.tools.registry import ToolRegistry
from tests.test_tool_selection import _session_with_caps, _tool


@pytest.mark.parametrize("text", ["my emails", "my e-mails", "my mailbox", "my replies"])
def test_plural_mail_requests_are_not_chat_only(text):
    assert not is_conversational_turn(text)


def _catalog():
    registry = ToolRegistry()
    tools = [
        _tool("inbox.list", CapabilityKind.IMAP_READ),
        _tool("mail.imap.list_threads", CapabilityKind.IMAP_READ),
        _tool("mail.imap.read_message", CapabilityKind.IMAP_READ),
        _tool("policy.preview", CapabilityKind.READ_FS),
    ]
    for tool in tools:
        registry.register(tool)
    return registry, tools


@pytest.mark.parametrize("mode", ["static", "retrieve"])
def test_real_mail_selected_without_demo_in_small_and_curated_surfaces(mode):
    registry, tools = _catalog()
    session = _session_with_caps(CapabilityKind.IMAP_READ)
    result = select_tools_for_turn(
        registry,
        session,
        ExecutionMode.TURN_LEVEL,
        tools,
        user_message="summarize my emails",
        selection_config=ToolSelectionConfig(mode=mode, max_selected=3),
    )
    names = {t.name for t in result.selected}
    assert "inbox.list" not in names
    assert "mail.imap.list_threads" in names
    assert "mail.imap.read_message" in names


def test_followup_in_inbox_session_retains_real_mail_surface():
    registry, tools = _catalog()
    session = _session_with_caps(CapabilityKind.IMAP_READ)
    assert all(
        t.name != "inbox.list"
        for t in mail_tool_surface(registry, session, tools, "the latest five, please")
    )


def test_invisible_real_mail_does_not_fall_back_to_demo_or_grant_access():
    registry, tools = _catalog()
    session = _session_with_caps()
    assert mail_tool_surface(registry, session, tools[:1], "my emails") == []


def test_explicit_demo_request_keeps_demo_tools():
    registry, tools = _catalog()
    session = _session_with_caps(CapabilityKind.IMAP_READ)
    assert mail_tool_surface(registry, session, tools, "triage the demo inbox") == tools


def test_bounded_recent_summary_offers_only_projection_without_granting_it():
    registry, tools = _catalog()
    projection = _tool("quarantined.summarize_mail", CapabilityKind.IMAP_READ)
    registry.register(projection)
    session = _session_with_caps(CapabilityKind.IMAP_READ)
    assert mail_tool_surface(
        registry, session, [*tools, projection], "Summarize my two most recent emails."
    ) == [projection]
    assert (
        mail_tool_surface(registry, session, tools, "Summarize my two most recent emails.")
        == tools[1:]
    )
