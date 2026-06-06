"""Pattern (3) producer side — wrap_output_with_handles + make_handle_wrapper.

Closes the half-wired Pattern (3) gap: today the dispatcher binds
handle UUIDs back to values (consumer side, in tools/client.py),
but no tool actually issues handles in the first place. This module
gives operators a one-line wrapper so any read-tool can produce
handle-wrapped output, and the planner stays data-blind.

Tests pin:
  - sensitive keys are substituted with UUIDs in the visible output
  - non-sensitive keys pass through unchanged
  - the store can bind the UUID back to the real value
  - the wrapper integrates with the existing ToolDefinition shape
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from capabledeputy.patterns.reference_handle import (
    ReferenceHandleStore,
    ResolvedLabels,
    is_planner_safe_token,
    make_handle_wrapper,
    wrap_output_with_handles,
)


async def test_wrap_output_substitutes_sensitive_keys() -> None:
    store = ReferenceHandleStore()
    sid = uuid4()
    out = await wrap_output_with_handles(
        store=store,
        session_id=sid,
        output={"record": "patient's full medical history", "key": "alice"},
        sensitive_keys=("record",),
        labels=ResolvedLabels(axis_a=("health",), axis_b=("source-declared",)),
    )
    # 'record' is now a UUID string; 'key' is unchanged.
    assert is_planner_safe_token(out["record"])
    assert out["key"] == "alice"
    # The store can resolve the handle back to the original value.
    handle_id = UUID(out["record"])
    assert store.has_handle(handle_id)
    real = store.bind(
        session_id=sid,
        handle_id=handle_id,
        destination_canonical_id="x",
        tool="t",
        audit_id=uuid4(),
    )
    assert real == "patient's full medical history"


async def test_wrap_output_with_empty_sensitive_keys_is_noop() -> None:
    store = ReferenceHandleStore()
    sid = uuid4()
    out = await wrap_output_with_handles(
        store=store,
        session_id=sid,
        output={"x": "y", "a": "b"},
        sensitive_keys=(),
    )
    assert out == {"x": "y", "a": "b"}


async def test_wrap_output_non_sensitive_keys_pass_through() -> None:
    store = ReferenceHandleStore()
    sid = uuid4()
    out = await wrap_output_with_handles(
        store=store,
        session_id=sid,
        output={
            "record": "secret",
            "metadata": {"id": 1},  # non-sensitive
            "key": "k",
        },
        sensitive_keys=("record",),
    )
    assert is_planner_safe_token(out["record"])
    assert out["metadata"] == {"id": 1}
    assert out["key"] == "k"


async def test_make_handle_wrapper_wraps_tool_handler() -> None:
    """The decorator factory: any existing tool handler can be wrapped
    to issue handles on configured keys without modifying the handler."""
    from capabledeputy.tools.registry import ToolContext, ToolResult

    async def raw_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return ToolResult(output={"secret_value": "the-secret", "key": args["key"]})

    store = ReferenceHandleStore()
    wrapped = make_handle_wrapper(
        raw_handler,
        store=store,
        sensitive_keys=("secret_value",),
        labels=ResolvedLabels(axis_a=("personal",)),
    )

    from capabledeputy.policy.labels import LabelState

    sid = uuid4()
    ctx = ToolContext(session_id=sid, label_state=LabelState())
    result = await wrapped({"key": "alice"}, ctx)
    assert isinstance(result, ToolResult)
    assert is_planner_safe_token(result.output["secret_value"])
    assert result.output["key"] == "alice"
    # The store has the bound value.
    handle_id = UUID(result.output["secret_value"])
    assert store.has_handle(handle_id)


async def test_wrap_output_does_not_mutate_input() -> None:
    store = ReferenceHandleStore()
    sid = uuid4()
    original = {"record": "secret", "key": "k"}
    out = await wrap_output_with_handles(
        store=store,
        session_id=sid,
        output=original,
        sensitive_keys=("record",),
    )
    # Original dict unchanged (no aliasing of internal state).
    assert original == {"record": "secret", "key": "k"}
    assert out["record"] != "secret"
