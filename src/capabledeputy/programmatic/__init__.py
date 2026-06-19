"""Programmatic execution mode (DESIGN.md §5.3, §10.5).

A Python-AST-subset interpreter that propagates labels through every
operation and routes tool calls through the same `LabeledToolClient`
used by turn-level mode. Two entry points:

  - run_program(source, ...)     — execute, gating each tool call
  - dry_run_program(source, ...) — symbolic trace; report violations

Both share the same evaluator; they differ only in whether the tool
caller dispatches for real or returns synthetic results.
"""

from __future__ import annotations

from capabledeputy.programmatic.bundle_runner import (
    BundleMismatchError,
    dry_run_for_bundle,
    execute_with_approved_bundle,
)
from capabledeputy.programmatic.errors import (
    ProgramPolicyError,
    ProgramRuntimeError,
    ProgramSyntaxError,
)
from capabledeputy.programmatic.evaluator import (
    ExecutionResult,
    ToolCallRecord,
    run_program,
)
from capabledeputy.programmatic.parser import parse_program
from capabledeputy.programmatic.runner import (
    DryRunReport,
    dry_run_program,
    format_return_value_for_planner,
    label_state_summary,
    return_value_payload,
    run_program_against_session,
)
from capabledeputy.programmatic.value import LabeledValue, labels_of, unwrap

__all__ = [
    "BundleMismatchError",
    "DryRunReport",
    "ExecutionResult",
    "LabeledValue",
    "ProgramPolicyError",
    "ProgramRuntimeError",
    "ProgramSyntaxError",
    "ToolCallRecord",
    "dry_run_for_bundle",
    "dry_run_program",
    "execute_with_approved_bundle",
    "format_return_value_for_planner",
    "label_state_summary",
    "labels_of",
    "parse_program",
    "return_value_payload",
    "run_program",
    "run_program_against_session",
    "unwrap",
]
