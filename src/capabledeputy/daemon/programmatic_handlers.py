"""RPC handlers for the programmatic execution mode (DESIGN.md §5.3).

Two methods:
  - programmatic.dry_run: parse + symbolic-execute, returning the
    predicted tool-call sequence and any policy violations.
  - programmatic.run: parse + execute against a session, dispatching
    each tool call through LabeledToolClient (real policy + audit).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from capabledeputy.app import App
from capabledeputy.daemon.handlers import Handler
from capabledeputy.programmatic import (
    ProgramSyntaxError,
    dry_run_program,
    run_program_against_session,
)
from capabledeputy.programmatic.evaluator import ToolCallRecord


def _record_to_dict(record: ToolCallRecord) -> dict[str, Any]:
    return {
        "tool": record.tool_name,
        "args": record.args,
        "arg_labels": sorted(label.value for label in record.arg_labels),
        "decision": record.decision.value,
        "inherent_labels": sorted(label.value for label in record.inherent_labels),
        "rule": record.rule,
        "reason": record.reason,
        "line": record.line,
    }


def make_programmatic_handlers(app: App) -> dict[str, Handler]:
    async def programmatic_dry_run(params: dict[str, Any]) -> dict[str, Any]:
        source = str(params["source"])
        report = await dry_run_program(source, app.registry)
        return {
            "ok": report.ok,
            "parse_error": report.parse_error,
            "runtime_error": report.runtime_error,
            "tool_calls": [_record_to_dict(c) for c in report.tool_calls],
            "violations": [_record_to_dict(c) for c in report.violations],
        }

    async def programmatic_run(params: dict[str, Any]) -> dict[str, Any]:
        source = str(params["source"])
        session_id = UUID(params["session_id"])
        try:
            result = await run_program_against_session(
                source,
                session_id=session_id,
                tool_client=app.tool_client,
                graph=app.graph,
                registry=app.registry,
                audit=app.audit,
            )
        except ProgramSyntaxError as e:
            return {
                "ok": False,
                "parse_error": str(e),
                "tool_calls": [],
                "error": None,
                "return_value": None,
            }
        return {
            "ok": result.error is None,
            "parse_error": None,
            "tool_calls": [_record_to_dict(c) for c in result.tool_calls],
            "error": result.error,
            "return_value": (
                None
                if result.return_value is None
                else {
                    "raw": result.return_value.raw,
                    "label_state": result.return_value.label_state.to_dict(),
                }
            ),
        }

    return {
        "programmatic.dry_run": programmatic_dry_run,
        "programmatic.run": programmatic_run,
    }
