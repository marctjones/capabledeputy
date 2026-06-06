"""Policy script host (spec 004 P3 foundation).

Operators want to author policy refinements in a sandboxable language
(Starlark, Lua, WebAssembly, etc.) rather than full Python with TCB
trust. The PolicyScriptHost port abstracts over runtime choice so the
chokepoint can call a script-authored DecisionInspector regardless of
implementation language.

Design:
  - Operator writes a policy script (e.g., `policies/after_hours.star`)
  - Daemon loads it via a registered HostFactory
  - Script is compiled / parsed once at load time
  - At decision time, chokepoint calls script.evaluate(action, session,
    proposed_outcome) — returns relax/tighten/none
  - Host enforces resource limits: step count, memory, timeout
  - Errors surface in audit, don't crash the chokepoint

This commit lands the PORT + a pure-Python reference implementation
(SafePythonScriptHost — uses RestrictedPython-style AST inspection +
small evaluator). Operators that need the real Starlark / Wasm
guarantees configure starlark-rust + PyO3 (P3 deferred work) or
wasmtime-py (P4 deferred work).

The pure-Python reference is NOT a security boundary; it's a
*structural* enforcement of the same policy language operators
would write for Starlark/Wasm. Trust comes from the operator
authoring + reviewing the script, not the runtime sandbox.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class PolicyScript:
    """A loaded policy script.

    Attributes:
        name: Operator-visible identifier (filename without extension).
        source: The script source text (for audit + replay).
        runtime_kind: "starlark" | "lua" | "python-reference" | "wasm"
        compiled_marker: Implementation-specific compiled form (opaque).
        step_limit: Maximum execution steps per call (resource bound).
    """

    name: str
    source: str
    runtime_kind: str
    compiled_marker: Any = None
    step_limit: int = 100_000


@dataclass(frozen=True)
class ScriptOutcome:
    """Result of one script.evaluate() call.

    The script's logic produces a relax / tighten / abstain signal in
    the same shape DecisionInspector does — this is the substrate that
    feeds the DecisionInspector port from an operator-authored script.
    """

    kind: str  # "relax" | "tighten" | "abstain" | "error"
    to_decision: str | None = None
    rule: str = ""
    rationale: str = ""
    error: str = ""
    steps_used: int = 0


class PolicyScriptHost(Protocol):
    """Host contract for a script-running runtime.

    Each implementation (starlark-rust, lua, pure-python-ref) provides:
      - compile(): parse + validate the source; raise on bad syntax
      - evaluate(): run with operator-supplied inputs; return outcome

    Implementations MUST enforce:
      - Step / instruction limit (bounded execution)
      - No I/O (deterministic; no network/filesystem inside the script)
      - Resource bounds (memory, optional CPU timeout)
    """

    runtime_kind: str

    def compile(self, name: str, source: str) -> PolicyScript:
        """Parse + validate `source`; return a compiled PolicyScript.

        Raise on syntax errors / disallowed constructs.
        """
        ...

    async def evaluate(
        self,
        script: PolicyScript,
        *,
        action: dict[str, Any],
        session: dict[str, Any],
        proposed_outcome: dict[str, Any],
    ) -> ScriptOutcome:
        """Run the compiled script with operator-supplied inputs.

        Inputs are plain dicts (serializable across language boundaries).
        Result is the outcome the host extracted from the script.
        """
        ...


class SafePythonScriptHost:
    """Pure-Python reference implementation.

    NOT a security sandbox — it's a structural enforcement of the
    policy-language CONTRACT so operators can prototype scripts before
    binding to Starlark/Lua/Wasm. The author of the script is trusted
    by configuration (the script file lives in the operator's policies/
    directory which the daemon owns).

    Restrictions enforced (best-effort, not security):
      - Source must be a single function definition named `inspect(...)`
      - Whitelisted globals (no `__import__`, no `open`, no `eval`)
      - Step-counted via sys.settrace for line-level metering

    For real security guarantees use the Starlark host (P3 deferred).
    """

    runtime_kind: str = "python-reference"

    def compile(self, name: str, source: str) -> PolicyScript:
        # Basic shape check: must define an `inspect` callable.
        if "def inspect(" not in source:
            raise ValueError(
                f"policy script {name!r} must define `def inspect(...)`",
            )
        # AST-level validation: forbid certain top-level constructs.
        import ast

        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            raise ValueError(f"policy script {name!r} has syntax error: {e}") from e

        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                raise ValueError(
                    f"policy script {name!r} may not use `import` "
                    "(pure-Python ref host enforces hermetic execution)",
                )
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "__builtins__"
            ):
                raise ValueError(
                    f"policy script {name!r} may not reference __builtins__",
                )
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ("eval", "exec", "compile", "__import__", "open")
            ):
                raise ValueError(
                    f"policy script {name!r} may not call {node.func.id!r}",
                )

        # Compile to bytecode for later exec
        try:
            compiled = compile(source, f"<policy:{name}>", "exec")
        except SyntaxError as e:
            raise ValueError(
                f"policy script {name!r} compile failed: {e}",
            ) from e

        return PolicyScript(
            name=name,
            source=source,
            runtime_kind=self.runtime_kind,
            compiled_marker=compiled,
        )

    async def evaluate(
        self,
        script: PolicyScript,
        *,
        action: dict[str, Any],
        session: dict[str, Any],
        proposed_outcome: dict[str, Any],
    ) -> ScriptOutcome:
        # Step-counted exec via sys.settrace
        import sys

        steps = {"count": 0}
        step_limit = script.step_limit

        def _trace(frame, event, arg):
            if event == "line":
                steps["count"] += 1
                if steps["count"] > step_limit:
                    raise RuntimeError(
                        f"policy script {script.name!r} exceeded step limit ({step_limit})",
                    )
            return _trace

        # Whitelisted globals — no builtins, helper functions only
        helpers = {
            "relax": lambda *, to, rule, rationale="": {
                "kind": "relax",
                "to": to,
                "rule": rule,
                "rationale": rationale,
            },
            "tighten": lambda *, to, rule, rationale="": {
                "kind": "tighten",
                "to": to,
                "rule": rule,
                "rationale": rationale,
            },
            "abstain": lambda: None,
        }
        # Minimal builtins — only what's needed for safe-ish logic
        safe_builtins = {
            "len": len,
            "str": str,
            "int": int,
            "float": float,
            "bool": bool,
            "True": True,
            "False": False,
            "None": None,
            "isinstance": isinstance,
            "min": min,
            "max": max,
            "abs": abs,
            "any": any,
            "all": all,
            "sorted": sorted,
            "list": list,
            "dict": dict,
            "tuple": tuple,
            "set": set,
            "frozenset": frozenset,
        }
        namespace: dict[str, Any] = dict(helpers)
        namespace["__builtins__"] = safe_builtins

        try:
            sys.settrace(_trace)
            exec(script.compiled_marker, namespace)
            inspect_fn = namespace.get("inspect")
            if not callable(inspect_fn):
                return ScriptOutcome(
                    kind="error",
                    error=f"script {script.name!r} did not bind `inspect`",
                )
            result = inspect_fn(
                action=action,
                session=session,
                proposed_outcome=proposed_outcome,
            )
        except Exception as e:
            return ScriptOutcome(
                kind="error",
                error=str(e),
                steps_used=steps["count"],
            )
        finally:
            sys.settrace(None)

        if result is None:
            return ScriptOutcome(kind="abstain", steps_used=steps["count"])
        if not isinstance(result, dict):
            return ScriptOutcome(
                kind="error",
                error=f"script {script.name!r} returned non-dict: {type(result).__name__}",
                steps_used=steps["count"],
            )
        kind = result.get("kind", "")
        if kind not in ("relax", "tighten"):
            return ScriptOutcome(
                kind="error",
                error=f"script {script.name!r} returned unknown kind {kind!r}",
                steps_used=steps["count"],
            )
        return ScriptOutcome(
            kind=kind,
            to_decision=str(result.get("to", "")),
            rule=str(result.get("rule", "")),
            rationale=str(result.get("rationale", "")),
            steps_used=steps["count"],
        )
