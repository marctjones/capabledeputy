"""#320 — app-level timeouts: a hung awaitable is cancelled and surfaced as a
labeled OperationTimeoutError, never stalls the caller."""

from __future__ import annotations

import anyio
import pytest

from capabledeputy.reliability import (
    OperationTimeoutError,
    default_llm_timeout_seconds,
    default_tool_timeout_seconds,
    with_timeout,
)
from capabledeputy.reliability.timeout import _env_seconds


async def test_fast_operation_passes_through() -> None:
    async def _fast() -> str:
        return "ok"

    assert await with_timeout(5.0, "fast", _fast) == "ok"


async def test_hung_operation_is_cancelled_and_labeled() -> None:
    cancelled = {"hit": False}

    async def _hang() -> None:
        try:
            await anyio.sleep(100)
        except anyio.get_cancelled_exc_class():
            cancelled["hit"] = True
            raise

    with pytest.raises(OperationTimeoutError, match=r"my-op exceeded its 0\.05s"):
        await with_timeout(0.05, "my-op", _hang)
    # the underlying task was actually cancelled, not orphaned.
    assert cancelled["hit"] is True


async def test_operation_timeout_is_a_timeout_error() -> None:
    async def _hang() -> None:
        await anyio.sleep(100)

    with pytest.raises(TimeoutError):  # subclass — existing handlers still catch it
        await with_timeout(0.05, "op", _hang)


async def test_inner_exception_propagates_untouched() -> None:
    async def _boom() -> None:
        raise ValueError("inner")

    with pytest.raises(ValueError, match="inner"):
        await with_timeout(5.0, "op", _boom)


def test_env_defaults_fail_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAPDEP_LLM_TIMEOUT_SECONDS", raising=False)
    assert default_llm_timeout_seconds() == 120.0
    assert default_tool_timeout_seconds() == 60.0

    monkeypatch.setenv("CAPDEP_LLM_TIMEOUT_SECONDS", "30")
    assert default_llm_timeout_seconds() == 30.0
    # non-positive / garbage -> default (never "no timeout").
    monkeypatch.setenv("CAPDEP_LLM_TIMEOUT_SECONDS", "0")
    assert default_llm_timeout_seconds() == 120.0
    monkeypatch.setenv("CAPDEP_LLM_TIMEOUT_SECONDS", "nope")
    assert default_llm_timeout_seconds() == 120.0


def test_env_seconds_helper() -> None:
    import os

    os.environ.pop("X_TEST_T", None)
    assert _env_seconds("X_TEST_T", 7.0) == 7.0


# --- the litellm client applies the timeout ------------------------------


async def test_litellm_client_times_out_a_hung_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hung litellm.acompletion is cancelled by the client's app-level deadline
    and surfaced as OperationTimeoutError instead of stalling the turn."""
    import sys
    import types

    from capabledeputy.llm.litellm_client import LiteLLMClient

    fake = types.ModuleType("litellm")

    async def _hang(**_kwargs):
        await anyio.sleep(100)

    fake.acompletion = _hang
    monkeypatch.setitem(sys.modules, "litellm", fake)

    client = LiteLLMClient(model="test-model", timeout_seconds=0.05)
    with pytest.raises(OperationTimeoutError, match="LLM completion"):
        await client.respond(messages=[], tools=[])


def test_litellm_client_defaults_timeout_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from capabledeputy.llm.litellm_client import LiteLLMClient

    monkeypatch.setenv("CAPDEP_LLM_TIMEOUT_SECONDS", "42")
    assert LiteLLMClient(model="m")._timeout_seconds == 42.0
    assert LiteLLMClient(model="m", timeout_seconds=5.0)._timeout_seconds == 5.0
