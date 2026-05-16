"""Programmatic-mode evaluator: label propagation through ops + calls.

Every operation produces a result whose label set is the union of its
inputs' labels. Tool calls run through a caller-supplied hook; we use a
controllable in-memory hook here that lets each test assert on the
propagated labels at every step.
"""

from __future__ import annotations

from typing import Any

import pytest

from capabledeputy.policy.labels import Label
from capabledeputy.policy.rules import Decision
from capabledeputy.programmatic.evaluator import (
    ToolDispatchResult,
    run_program,
)
from capabledeputy.programmatic.parser import parse_program
from capabledeputy.programmatic.value import LabeledValue


def _scripted_caller(scripted: dict[str, ToolDispatchResult]):
    async def caller(
        tool_name: str,
        args: dict[str, Any],
        arg_labels: frozenset[Label],
    ) -> ToolDispatchResult:
        if tool_name not in scripted:
            return ToolDispatchResult(
                decision=Decision.DENY,
                rule=None,
                reason=f"unknown tool {tool_name}",
            )
        return scripted[tool_name]

    return caller


async def test_constants_have_no_labels() -> None:
    module = parse_program("x = 1 + 2\nresult = x * 3\n")
    result = await run_program(module, _scripted_caller({}))
    assert result.error is None
    assert result.final_scope["result"].raw == 9
    assert result.final_scope["result"].labels == frozenset()


async def test_tool_call_inherent_labels_propagate() -> None:
    module = parse_program('note = call("memory.read", key="x")\n')
    caller = _scripted_caller(
        {
            "memory.read": ToolDispatchResult(
                decision=Decision.ALLOW,
                output="prescription text",
                inherent_labels=frozenset({Label.CONFIDENTIAL_HEALTH}),
            ),
        },
    )
    result = await run_program(module, caller)
    note = result.final_scope["note"]
    assert note.raw == "prescription text"
    assert Label.CONFIDENTIAL_HEALTH in note.labels


async def test_binary_op_unions_operand_labels() -> None:
    module = parse_program(
        'a = call("read.health", key="x")\nb = call("read.fin", key="y")\ncombined = a + b\n',
    )
    caller = _scripted_caller(
        {
            "read.health": ToolDispatchResult(
                decision=Decision.ALLOW,
                output="H",
                inherent_labels=frozenset({Label.CONFIDENTIAL_HEALTH}),
            ),
            "read.fin": ToolDispatchResult(
                decision=Decision.ALLOW,
                output="F",
                inherent_labels=frozenset({Label.CONFIDENTIAL_FINANCIAL}),
            ),
        },
    )
    result = await run_program(module, caller)
    combined = result.final_scope["combined"]
    assert combined.raw == "HF"
    assert Label.CONFIDENTIAL_HEALTH in combined.labels
    assert Label.CONFIDENTIAL_FINANCIAL in combined.labels


async def test_subscript_propagates_container_labels() -> None:
    module = parse_program('d = call("read", key="x")\nfirst = d["a"]\n')
    caller = _scripted_caller(
        {
            "read": ToolDispatchResult(
                decision=Decision.ALLOW,
                output={"a": 1, "b": 2},
                inherent_labels=frozenset({Label.CONFIDENTIAL_HEALTH}),
            ),
        },
    )
    result = await run_program(module, caller)
    first = result.final_scope["first"]
    assert first.raw == 1
    assert Label.CONFIDENTIAL_HEALTH in first.labels


async def test_for_loop_propagates_iterable_labels() -> None:
    module = parse_program(
        'data = call("read", key="x")\ntotal = 0\nfor v in data:\n    total = total + v\n',
    )
    caller = _scripted_caller(
        {
            "read": ToolDispatchResult(
                decision=Decision.ALLOW,
                output=[1, 2, 3],
                inherent_labels=frozenset({Label.CONFIDENTIAL_FINANCIAL}),
            ),
        },
    )
    result = await run_program(module, caller)
    total = result.final_scope["total"]
    assert total.raw == 6
    assert Label.CONFIDENTIAL_FINANCIAL in total.labels


async def test_tool_call_args_carry_labels_into_recorded_arg_labels() -> None:
    module = parse_program(
        'note = call("read.h", key="x")\n'
        'sent = call("send.email", body=note, to="alice@example.com")\n',
    )
    scripted = {
        "read.h": ToolDispatchResult(
            decision=Decision.ALLOW,
            output="confidential",
            inherent_labels=frozenset({Label.CONFIDENTIAL_HEALTH}),
        ),
        "send.email": ToolDispatchResult(decision=Decision.ALLOW, output={"ok": True}),
    }
    result = await run_program(module, _scripted_caller(scripted))
    assert result.error is None
    [_, send] = result.tool_calls
    assert send.tool_name == "send.email"
    assert Label.CONFIDENTIAL_HEALTH in send.arg_labels


async def test_policy_deny_halts_program() -> None:
    module = parse_program(
        'a = call("read", key="x")\nb = call("blocked", arg=a)\nc = 1\n',
    )
    scripted = {
        "read": ToolDispatchResult(decision=Decision.ALLOW, output="ok"),
        "blocked": ToolDispatchResult(
            decision=Decision.DENY,
            rule="health-meets-egress",
            reason="not allowed",
        ),
    }
    result = await run_program(module, _scripted_caller(scripted))
    assert result.error is not None
    assert "blocked" in result.error
    assert "c" not in result.final_scope


async def test_initial_scope_labels_propagate_through_use() -> None:
    initial = {
        "patient_id": LabeledValue(
            raw="P-42",
            labels=frozenset({Label.CONFIDENTIAL_HEALTH}),
        ),
    }
    module = parse_program('out = call("lookup", id=patient_id)\n')
    caller = _scripted_caller(
        {"lookup": ToolDispatchResult(decision=Decision.ALLOW, output={"r": 1})},
    )
    result = await run_program(module, caller, initial_scope=initial)
    assert result.error is None
    [call] = result.tool_calls
    assert Label.CONFIDENTIAL_HEALTH in call.arg_labels


async def test_return_statement_returns_labeled_value() -> None:
    module = parse_program('out = call("x", key="y")\nreturn out\n')
    caller = _scripted_caller(
        {
            "x": ToolDispatchResult(
                decision=Decision.ALLOW,
                output=42,
                inherent_labels=frozenset({Label.CONFIDENTIAL_HEALTH}),
            ),
        },
    )
    result = await run_program(module, caller)
    assert result.return_value is not None
    assert result.return_value.raw == 42
    assert Label.CONFIDENTIAL_HEALTH in result.return_value.labels


async def test_unknown_tool_returns_deny() -> None:
    module = parse_program('call("nope", key="x")\n')
    result = await run_program(module, _scripted_caller({}))
    assert result.error is not None
    assert "nope" in result.error


async def test_attribute_call_pattern_was_already_blocked_at_parse() -> None:
    # Smoke check that our integration with parse_program means attribute
    # calls never reach the evaluator.
    from capabledeputy.programmatic.errors import ProgramSyntaxError

    with pytest.raises(ProgramSyntaxError):
        parse_program('s = "hi".upper()\n')


async def test_if_else_branch_selection() -> None:
    module = parse_program(
        "x = 5\nif x > 3:\n    y = 'big'\nelse:\n    y = 'small'\n",
    )
    result = await run_program(module, _scripted_caller({}))
    assert result.final_scope["y"].raw == "big"


async def test_builtin_len_propagates_labels_of_argument() -> None:
    module = parse_program(
        'data = call("read", key="x")\nn = len(data)\n',
    )
    caller = _scripted_caller(
        {
            "read": ToolDispatchResult(
                decision=Decision.ALLOW,
                output="hello",
                inherent_labels=frozenset({Label.CONFIDENTIAL_HEALTH}),
            ),
        },
    )
    result = await run_program(module, caller)
    n = result.final_scope["n"]
    assert n.raw == 5
    assert Label.CONFIDENTIAL_HEALTH in n.labels


async def test_aug_assign_unions_labels() -> None:
    module = parse_program(
        'a = call("read", key="x")\ntotal = 0\ntotal += a\n',
    )
    caller = _scripted_caller(
        {
            "read": ToolDispatchResult(
                decision=Decision.ALLOW,
                output=10,
                inherent_labels=frozenset({Label.CONFIDENTIAL_FINANCIAL}),
            ),
        },
    )
    result = await run_program(module, caller)
    total = result.final_scope["total"]
    assert total.raw == 10
    assert Label.CONFIDENTIAL_FINANCIAL in total.labels


async def test_program_policy_error_carries_rule_and_decision() -> None:
    module = parse_program('call("blocked", x=1)\n')
    caller = _scripted_caller(
        {
            "blocked": ToolDispatchResult(
                decision=Decision.DENY,
                rule="health-meets-egress",
                reason="reason",
            ),
        },
    )
    result = await run_program(module, caller)
    assert result.error is not None
    [recorded] = result.tool_calls
    assert recorded.decision == Decision.DENY
    assert recorded.rule == "health-meets-egress"
