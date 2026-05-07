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
    run_program_against_session,
)
from capabledeputy.programmatic.value import LabeledValue, labels_of, unwrap

__all__ = [
    "DryRunReport",
    "ExecutionResult",
    "LabeledValue",
    "ProgramPolicyError",
    "ProgramRuntimeError",
    "ProgramSyntaxError",
    "ToolCallRecord",
    "dry_run_program",
    "labels_of",
    "parse_program",
    "run_program",
    "run_program_against_session",
    "unwrap",
]
