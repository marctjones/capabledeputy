"""Tests for conversational turn detection."""

from __future__ import annotations

from capabledeputy.agent.chat_turn import CHAT_MAX_TOKENS, is_conversational_turn


def test_greetings_are_conversational() -> None:
    assert is_conversational_turn("hi")
    assert is_conversational_turn("Hello there!")


def test_general_knowledge_is_conversational() -> None:
    assert is_conversational_turn("what is the capital of france")


def test_tool_intent_is_not_conversational() -> None:
    assert not is_conversational_turn("search my inbox for urgent mail")
    assert not is_conversational_turn("read the file on my desktop")


def test_chat_max_tokens_is_reasonable() -> None:
    assert CHAT_MAX_TOKENS == 512