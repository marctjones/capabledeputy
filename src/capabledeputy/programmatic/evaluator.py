"""AST-walking interpreter with label propagation.

Every value the interpreter handles is a `LabeledValue`. Operations
produce results whose labels are the union of the operands' labels.
The single tool entry point is the builtin `call(tool_name, **kwargs)`,
which delegates to a caller-supplied async hook so the same evaluator
serves both real execution (LabeledToolClient dispatch) and dry-run
(symbolic execution that records calls without side effects).
"""

from __future__ import annotations

import ast
import operator as op
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from capabledeputy.policy.labels import LabelState, most_restrictive_inherit
from capabledeputy.policy.rules import Decision
from capabledeputy.programmatic.errors import (
    ProgramPolicyError,
    ProgramRuntimeError,
)
from capabledeputy.programmatic.value import (
    LabeledValue,
    lv,
    tags_of,
    union_tags,
    unwrap,
)

# An async hook the caller provides to actually invoke a tool.
# Returns: ToolDispatchResult with decision, output, inherent_labels, rule, reason
ToolCaller = Callable[
    [str, dict[str, Any], LabelState],
    Awaitable["ToolDispatchResult"],
]


@dataclass(frozen=True)
class ToolDispatchResult:
    """Caller-side outcome of a tool dispatch in programmatic mode.

    `decision` is the policy decision; ALLOW means `output` is valid and
    `tags_added` should be propagated. Any other decision halts the
    program with a ProgramPolicyError before the caller sees `output`.
    """

    decision: Decision
    output: Any = None
    tags_added: LabelState = field(default_factory=LabelState)
    rule: str | None = None
    reason: str | None = None


@dataclass
class ToolCallRecord:
    tool_name: str
    args: dict[str, Any]
    arg_labels: frozenset[str]
    decision: Decision
    inherent_labels: frozenset[str]
    rule: str | None
    reason: str | None
    line: int | None


@dataclass
class ExecutionResult:
    return_value: LabeledValue | None
    tool_calls: list[ToolCallRecord]
    final_scope: dict[str, LabeledValue] = field(default_factory=dict)
    error: str | None = None


_BIN_OPS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.FloorDiv: op.floordiv,
    ast.Mod: op.mod,
    ast.Pow: op.pow,
    ast.LShift: op.lshift,
    ast.RShift: op.rshift,
    ast.BitOr: op.or_,
    ast.BitAnd: op.and_,
    ast.BitXor: op.xor,
}

_CMP_OPS: dict[type[ast.cmpop], Callable[[Any, Any], bool]] = {
    ast.Eq: op.eq,
    ast.NotEq: op.ne,
    ast.Lt: op.lt,
    ast.LtE: op.le,
    ast.Gt: op.gt,
    ast.GtE: op.ge,
    ast.Is: op.is_,
    ast.IsNot: op.is_not,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}

_UNARY_OPS: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.UAdd: op.pos,
    ast.USub: op.neg,
    ast.Not: op.not_,
    ast.Invert: op.invert,
}


SAFE_BUILTINS: dict[str, Any] = {
    "len": len,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
    "tuple": tuple,
    "set": set,
    "min": min,
    "max": max,
    "sum": sum,
    "sorted": sorted,
    "reversed": lambda x: list(reversed(x)),
    "range": lambda *a: list(range(*a)),
    "enumerate": lambda x: list(enumerate(x)),
    "zip": lambda *a: list(zip(*a, strict=False)),
    "abs": abs,
    "round": round,
    "any": any,
    "all": all,
}


class _ReturnSignal(Exception):  # noqa: N818 — control-flow signal, not error
    def __init__(self, value: LabeledValue | None) -> None:
        self.value = value


class _BreakSignal(Exception):  # noqa: N818
    pass


class _ContinueSignal(Exception):  # noqa: N818
    pass


class Evaluator:
    def __init__(
        self,
        tool_caller: ToolCaller,
        initial_scope: dict[str, LabeledValue] | None = None,
        builtins: dict[str, Any] | None = None,
    ) -> None:
        self._tool_caller = tool_caller
        self._scope: dict[str, LabeledValue] = dict(initial_scope or {})
        self._builtins: dict[str, Any] = dict(SAFE_BUILTINS)
        if builtins:
            self._builtins.update(builtins)
        self.tool_calls: list[ToolCallRecord] = []

    async def run(self, module: ast.Module) -> ExecutionResult:
        try:
            await self._exec_block(module.body)
        except _ReturnSignal as r:
            return ExecutionResult(
                return_value=r.value,
                tool_calls=self.tool_calls,
                final_scope=dict(self._scope),
            )
        except ProgramPolicyError as p:
            return ExecutionResult(
                return_value=None,
                tool_calls=self.tool_calls,
                final_scope=dict(self._scope),
                error=str(p),
            )
        return ExecutionResult(
            return_value=None,
            tool_calls=self.tool_calls,
            final_scope=dict(self._scope),
        )

    async def _exec_block(self, stmts: list[ast.stmt]) -> None:
        for stmt in stmts:
            await self._exec(stmt)

    async def _exec(self, stmt: ast.stmt) -> None:
        if isinstance(stmt, ast.Pass):
            return
        if isinstance(stmt, ast.Break):
            raise _BreakSignal
        if isinstance(stmt, ast.Continue):
            raise _ContinueSignal
        if isinstance(stmt, ast.Return):
            raw_value = await self._eval(stmt.value) if stmt.value else None
            wrapped = (
                raw_value
                if raw_value is None or isinstance(raw_value, LabeledValue)
                else lv(raw_value)
            )
            raise _ReturnSignal(wrapped)
        if isinstance(stmt, ast.Expr):
            await self._eval(stmt.value)
            return
        if isinstance(stmt, ast.Assign):
            value = await self._eval(stmt.value)
            wrapped = value if isinstance(value, LabeledValue) else lv(value)
            for target in stmt.targets:
                self._assign(target, wrapped)
            return
        if isinstance(stmt, ast.AugAssign):
            current = await self._eval(stmt.target)
            value = await self._eval(stmt.value)
            new_raw = _apply_binop(type(stmt.op), unwrap(current), unwrap(value))
            new = lv(new_raw, union_tags(current, value))
            self._assign(stmt.target, new)
            return
        if isinstance(stmt, ast.If):
            cond = await self._eval(stmt.test)
            if unwrap(cond):
                await self._exec_block(stmt.body)
            else:
                await self._exec_block(stmt.orelse)
            return
        if isinstance(stmt, ast.For):
            iterable = await self._eval(stmt.iter)
            seq = unwrap(iterable)
            seq_labels = tags_of(iterable)
            broke = False
            for item in seq:
                if isinstance(item, LabeledValue):
                    bound: LabeledValue = item.with_tags(seq_labels)
                else:
                    bound = lv(item, seq_labels)
                self._assign(stmt.target, bound)
                try:
                    await self._exec_block(stmt.body)
                except _BreakSignal:
                    broke = True
                    break
                except _ContinueSignal:
                    continue
            if not broke:
                await self._exec_block(stmt.orelse)
            return
        raise ProgramRuntimeError(f"unsupported statement {type(stmt).__name__}")

    def _assign(self, target: ast.expr, value: LabeledValue) -> None:
        if isinstance(target, ast.Name):
            self._scope[target.id] = value
            return
        if isinstance(target, ast.Tuple):
            seq = unwrap(value)
            seq_labels = tags_of(value)
            if not isinstance(seq, (list, tuple)):
                raise ProgramRuntimeError("cannot unpack non-sequence in assignment")
            if len(seq) != len(target.elts):
                raise ProgramRuntimeError(
                    f"unpack mismatch: expected {len(target.elts)}, got {len(seq)}",
                )
            for sub_target, item in zip(target.elts, seq, strict=True):
                if isinstance(item, LabeledValue):
                    bound = item.with_tags(seq_labels)
                else:
                    bound = lv(item, seq_labels)
                self._assign(sub_target, bound)
            return
        if isinstance(target, ast.Subscript):
            container = self._scope.get(_subscript_root_name(target))
            if container is None:
                raise ProgramRuntimeError("subscript assignment to undefined name")
            raw_container = unwrap(container)
            key = unwrap(self._eval_sync(target.slice))
            if isinstance(raw_container, (dict, list)):
                raw_container[key] = unwrap(value)
            else:
                raise ProgramRuntimeError(
                    f"cannot subscript-assign into {type(raw_container).__name__}",
                )
            new_labels = most_restrictive_inherit(container.label_state, value.label_state)
            self._scope[_subscript_root_name(target)] = lv(raw_container, new_labels)
            return
        raise ProgramRuntimeError(f"unsupported assignment target {type(target).__name__}")

    def _eval_sync(self, node: ast.expr) -> Any:
        # Used only for subscript indices, which must be label-pure constants
        # or already-resolved names.
        if isinstance(node, ast.Constant):
            return lv(node.value)
        if isinstance(node, ast.Name):
            if node.id in self._scope:
                return self._scope[node.id]
            if node.id in self._builtins:
                return lv(self._builtins[node.id])
            raise ProgramRuntimeError(f"undefined name in subscript: {node.id}")
        raise ProgramRuntimeError(
            f"non-constant subscript index requires async eval: {type(node).__name__}",
        )

    async def _eval(self, expr: ast.expr) -> LabeledValue:
        if isinstance(expr, ast.Constant):
            return lv(expr.value)
        if isinstance(expr, ast.Name):
            if expr.id in self._scope:
                return self._scope[expr.id]
            if expr.id in self._builtins:
                return lv(self._builtins[expr.id])
            raise ProgramRuntimeError(f"undefined name: {expr.id}")
        if isinstance(expr, ast.BinOp):
            left = await self._eval(expr.left)
            right = await self._eval(expr.right)
            value = _apply_binop(type(expr.op), unwrap(left), unwrap(right))
            return lv(value, union_tags(left, right))
        if isinstance(expr, ast.BoolOp):
            results: list[LabeledValue] = []
            for v in expr.values:
                r = await self._eval(v)
                results.append(r)
                if isinstance(expr.op, ast.And) and not unwrap(r):
                    return lv(unwrap(r), union_tags(*results))
                if isinstance(expr.op, ast.Or) and unwrap(r):
                    return lv(unwrap(r), union_tags(*results))
            return results[-1].with_tags(union_tags(*results))
        if isinstance(expr, ast.Compare):
            left = await self._eval(expr.left)
            current_raw = unwrap(left)
            all_labels = tags_of(left)
            result_value = True
            for cmp_op, comparator in zip(expr.ops, expr.comparators, strict=True):
                right = await self._eval(comparator)
                all_labels = most_restrictive_inherit(all_labels, tags_of(right))
                cmp_fn = _CMP_OPS[type(cmp_op)]
                step = cmp_fn(current_raw, unwrap(right))
                if not step:
                    result_value = False
                    break
                current_raw = unwrap(right)
            return lv(result_value, all_labels)
        if isinstance(expr, ast.UnaryOp):
            operand = await self._eval(expr.operand)
            return lv(_UNARY_OPS[type(expr.op)](unwrap(operand)), tags_of(operand))
        if isinstance(expr, ast.IfExp):
            cond = await self._eval(expr.test)
            chosen = await self._eval(expr.body) if unwrap(cond) else await self._eval(expr.orelse)
            return chosen.with_tags(tags_of(cond))
        if isinstance(expr, ast.Tuple):
            items = [await self._eval(e) for e in expr.elts]
            return lv(tuple(items), union_tags(*items))
        if isinstance(expr, ast.List):
            items = [await self._eval(e) for e in expr.elts]
            return lv(list(items), union_tags(*items))
        if isinstance(expr, ast.Set):
            items = [await self._eval(e) for e in expr.elts]
            return lv({unwrap(i) for i in items}, union_tags(*items))
        if isinstance(expr, ast.Dict):
            entries: dict[Any, Any] = {}
            collected_labels: LabelState = LabelState()
            for k_node, v_node in zip(expr.keys, expr.values, strict=True):
                if k_node is None:
                    raise ProgramRuntimeError("dict unpacking is not allowed")
                k = await self._eval(k_node)
                v = await self._eval(v_node)
                entries[unwrap(k)] = v
                collected_labels = most_restrictive_inherit(
                    collected_labels, tags_of(k), tags_of(v)
                )
            return lv(entries, collected_labels)
        if isinstance(expr, ast.Subscript):
            container = await self._eval(expr.value)
            index = await self._eval(expr.slice)
            raw_container = unwrap(container)
            raw_index = unwrap(index)
            try:
                value = raw_container[raw_index]
            except Exception as e:
                raise ProgramRuntimeError(
                    f"subscript failed: {type(e).__name__}: {e}",
                ) from e
            child_labels = most_restrictive_inherit(tags_of(container), tags_of(index))
            if isinstance(value, LabeledValue):
                return value.with_tags(child_labels)
            return lv(value, child_labels)
        if isinstance(expr, ast.Call):
            return await self._eval_call(expr)
        raise ProgramRuntimeError(f"unsupported expression {type(expr).__name__}")

    async def _eval_call(self, call_node: ast.Call) -> LabeledValue:
        if not isinstance(call_node.func, ast.Name):
            raise ProgramRuntimeError(
                "only direct function calls are allowed (no attribute calls)",
            )
        func_name = call_node.func.id
        positional: list[LabeledValue] = []
        for a in call_node.args:
            positional.append(await self._eval(a))
        kwargs: dict[str, LabeledValue] = {}
        for kw in call_node.keywords:
            if kw.arg is None:
                raise ProgramRuntimeError("**kwargs unpacking is not allowed")
            kwargs[kw.arg] = await self._eval(kw.value)

        if func_name == "call":
            return await self._dispatch_tool(call_node, positional, kwargs)

        if func_name in self._builtins:
            fn = self._builtins[func_name]
            raw_args = [unwrap(a) for a in positional]
            raw_kwargs = {k: unwrap(v) for k, v in kwargs.items()}
            try:
                result = fn(*raw_args, **raw_kwargs)
            except Exception as e:
                raise ProgramRuntimeError(
                    f"builtin {func_name} failed: {type(e).__name__}: {e}",
                ) from e
            child_labels = union_tags(*positional, *kwargs.values())
            return lv(result, child_labels)

        raise ProgramRuntimeError(f"unknown function: {func_name}")

    async def _dispatch_tool(
        self,
        call_node: ast.Call,
        positional: list[LabeledValue],
        kwargs: dict[str, LabeledValue],
    ) -> LabeledValue:
        if not positional or not isinstance(unwrap(positional[0]), str):
            raise ProgramRuntimeError(
                "call() requires the tool name as its first positional argument",
            )
        if len(positional) != 1:
            raise ProgramRuntimeError(
                "call() takes exactly one positional argument (the tool name)",
            )
        tool_name = unwrap(positional[0])
        raw_args = {k: unwrap(v) for k, v in kwargs.items()}
        arg_labels = union_tags(*kwargs.values())

        outcome = await self._tool_caller(tool_name, raw_args, arg_labels)

        # TODO: ToolCallRecord still expects frozenset[Label]; migrate to LabelState
        # For now, convert LabelState back to empty frozenset placeholder
        record = ToolCallRecord(
            tool_name=tool_name,
            args=raw_args,
            arg_labels=frozenset(),  # placeholder; tags now stored in outcome.tags_added
            decision=outcome.decision,
            inherent_labels=frozenset(),  # placeholder; tags now stored in outcome.tags_added
            rule=outcome.rule,
            reason=outcome.reason,
            line=getattr(call_node, "lineno", None),
        )
        self.tool_calls.append(record)

        if outcome.decision != Decision.ALLOW:
            raise ProgramPolicyError(
                tool_name=tool_name,
                decision=outcome.decision,
                rule=outcome.rule,
                reason=outcome.reason,
            )

        return lv(outcome.output, union_tags(arg_labels, outcome.tags_added))


def _apply_binop(op_type: type[ast.operator], left: Any, right: Any) -> Any:
    fn = _BIN_OPS.get(op_type)
    if fn is None:
        raise ProgramRuntimeError(f"unsupported binary operator {op_type.__name__}")
    return fn(left, right)


def _subscript_root_name(node: ast.Subscript) -> str:
    if isinstance(node.value, ast.Name):
        return node.value.id
    raise ProgramRuntimeError("subscript-assign root must be a bare name")


async def run_program(
    module: ast.Module,
    tool_caller: ToolCaller,
    *,
    initial_scope: dict[str, LabeledValue] | None = None,
    builtins: dict[str, Any] | None = None,
) -> ExecutionResult:
    evaluator = Evaluator(
        tool_caller=tool_caller,
        initial_scope=initial_scope,
        builtins=builtins,
    )
    return await evaluator.run(module)
