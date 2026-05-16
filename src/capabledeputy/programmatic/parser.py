"""Parser for the Python-AST subset accepted by programmatic mode.

We accept Python source as written (LLMs already produce valid Python)
and reject any forbidden construct *before* the evaluator ever sees it.
This is the static gate — no untrusted code path can rely on `try/except`
or attribute writes to escape policy because the parser refuses to load
the program if any such node is present.

Allowed: literals, name reads, arithmetic / comparison / boolean / unary
operators, `if/else`, `for ... in ...`, function calls (positional + kw),
subscripting (read), assignments to bare names or subscripts, container
literals (list/dict/tuple/set), `pass`, `return` (top-level).

Forbidden: import, class, def, lambda, try/except, with, while, global,
nonlocal, del, decorators, comprehensions, generators, yield, await,
attribute writes, attribute reads (use builtin functions instead).
"""

from __future__ import annotations

import ast

from capabledeputy.programmatic.errors import ProgramSyntaxError

_FORBIDDEN_NODE_TYPES: tuple[type[ast.AST], ...] = (
    ast.Import,
    ast.ImportFrom,
    ast.ClassDef,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.Lambda,
    ast.Try,
    ast.TryStar,
    ast.With,
    ast.AsyncWith,
    ast.While,
    ast.Global,
    ast.Nonlocal,
    ast.Delete,
    ast.Yield,
    ast.YieldFrom,
    ast.Await,
    ast.AsyncFor,
    ast.GeneratorExp,
    ast.SetComp,
    ast.ListComp,
    ast.DictComp,
    ast.Raise,
    ast.Match,
    ast.NamedExpr,
)

_ALLOWED_STMT_TYPES: tuple[type[ast.AST], ...] = (
    ast.Assign,
    ast.AugAssign,
    ast.Expr,
    ast.If,
    ast.For,
    ast.Return,
    ast.Pass,
    ast.Break,
    ast.Continue,
)

_ALLOWED_EXPR_TYPES: tuple[type[ast.AST], ...] = (
    ast.Constant,
    ast.Name,
    ast.BinOp,
    ast.BoolOp,
    ast.Compare,
    ast.UnaryOp,
    ast.Call,
    ast.Subscript,
    ast.IfExp,
    ast.Tuple,
    ast.List,
    ast.Dict,
    ast.Set,
    ast.Slice,
    ast.Starred,
    ast.keyword,
    # operator and context nodes are leaves of allowed parents:
    ast.Load,
    ast.Store,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.Pow,
    ast.FloorDiv,
    ast.MatMult,
    ast.LShift,
    ast.RShift,
    ast.BitOr,
    ast.BitAnd,
    ast.BitXor,
    ast.UAdd,
    ast.USub,
    ast.Not,
    ast.Invert,
    ast.And,
    ast.Or,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.Is,
    ast.IsNot,
    ast.In,
    ast.NotIn,
)


def parse_program(source: str) -> ast.Module:
    try:
        module = ast.parse(source, mode="exec")
    except SyntaxError as e:
        raise ProgramSyntaxError(f"python parse error: {e.msg} at line {e.lineno}") from e
    _validate(module)
    return module


def _validate(module: ast.Module) -> None:
    for node in ast.walk(module):
        _validate_node(node)


def _validate_node(node: ast.AST) -> None:
    if isinstance(node, _FORBIDDEN_NODE_TYPES):
        raise ProgramSyntaxError(
            f"forbidden construct {type(node).__name__} at line {getattr(node, 'lineno', '?')}",
            node,
        )
    if isinstance(node, ast.Attribute):
        raise ProgramSyntaxError(
            "attribute access is not allowed in programmatic mode "
            f"(found .{node.attr} at line {getattr(node, 'lineno', '?')}); "
            "use the builtin functions (len, str, int, ...) or a tool call instead",
            node,
        )
    if isinstance(node, ast.Module):
        return
    if isinstance(node, (*_ALLOWED_STMT_TYPES, *_ALLOWED_EXPR_TYPES)):
        # also block attribute *targets* in assignments (Attribute under Store)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                _validate_assignment_target(target)
        if isinstance(node, ast.AugAssign):
            _validate_assignment_target(node.target)
        if isinstance(node, ast.For):
            _validate_assignment_target(node.target)
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg is None:
                    raise ProgramSyntaxError(
                        f"**kwargs unpacking is not allowed at line {getattr(node, 'lineno', '?')}",
                        node,
                    )
        return
    raise ProgramSyntaxError(
        f"unsupported AST node {type(node).__name__} at line {getattr(node, 'lineno', '?')}",
        node,
    )


def _validate_assignment_target(target: ast.expr) -> None:
    if isinstance(target, ast.Name):
        return
    if isinstance(target, ast.Tuple):
        for elt in target.elts:
            _validate_assignment_target(elt)
        return
    if isinstance(target, ast.Subscript):
        return
    raise ProgramSyntaxError(
        f"only assignment to names, tuples, or subscripts is allowed "
        f"(got {type(target).__name__} at line {getattr(target, 'lineno', '?')})",
        target,
    )
