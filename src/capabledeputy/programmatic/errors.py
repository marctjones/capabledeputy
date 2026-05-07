"""Errors raised by the programmatic-mode parser, evaluator, and analyzer."""

from __future__ import annotations

import ast

from capabledeputy.policy.rules import Decision


class ProgramSyntaxError(ValueError):
    """Source program uses a forbidden Python construct or fails to parse."""

    def __init__(self, message: str, node: ast.AST | None = None) -> None:
        super().__init__(message)
        self.node = node
        self.lineno: int | None = getattr(node, "lineno", None)


class ProgramRuntimeError(RuntimeError):
    """Runtime error during interpretation that isn't a policy violation."""


class ProgramPolicyError(RuntimeError):
    """Tool call would violate policy. Raised by the evaluator when the
    LabeledToolClient (or the dry-run mock) returns a non-ALLOW decision.
    The program halts; callers receive the tool name, decision, and rule
    that fired so they can audit and report it.
    """

    def __init__(
        self,
        tool_name: str,
        decision: Decision,
        rule: str | None,
        reason: str | None,
    ) -> None:
        super().__init__(
            f"policy {decision.value} on {tool_name}"
            + (f" (rule={rule})" if rule else "")
            + (f": {reason}" if reason else ""),
        )
        self.tool_name = tool_name
        self.decision = decision
        self.rule = rule
        self.reason = reason
