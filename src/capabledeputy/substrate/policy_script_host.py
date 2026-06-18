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

Two implementations live here:

  - ``SafePythonScriptHost`` — pure-Python reference. NOT a security
    boundary; best-effort AST filtering of Python. Useful for
    prototyping the policy-language contract.
  - ``StarlarkScriptHost`` — the real sandbox (spec-004 P3), backed by
    starlark-rust via the ``starlark-pyo3`` binding (optional extra
    ``capabledeputy[starlark]``). Starlark is a *language-level* sandbox:
    a policy script has NO access to Python builtins, ``import``, the
    filesystem, the network, or any host object except the plain
    ``action`` / ``session`` / ``proposed_outcome`` dicts and the
    ``relax`` / ``tighten`` / ``abstain`` helpers. That language
    isolation is the boundary the AST-filtered Python host cannot give.

Both share one policy-language contract: a script defines
``inspect(action, session, proposed_outcome)`` returning
``relax(...)`` / ``tighten(...)`` / ``abstain()``.

Trust model + residual risks for the Starlark host are documented in
``specs/004-mcp-and-substrate/starlark-policy-host-threat-model.md``
(the WebAssembly/wasmtime host was dropped — Starlark covers the same
operator need at lower complexity; see ROADMAP).
"""

from __future__ import annotations

import multiprocessing
import queue
from dataclasses import dataclass
from typing import Any, Protocol, cast


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
    timeout_seconds: float = 1.0


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


# Starlark prelude: the policy-language helpers, defined IN Starlark so a
# script's `relax(...)` / `tighten(...)` / `abstain()` return the same
# dict shape the SafePythonScriptHost helpers produce. Prepended to the
# operator's source before parsing. (Starlark has no kw-only `*` syntax,
# so these take ordinary params; callers may still pass them by keyword.)
_STARLARK_PRELUDE = """
def relax(to, rule, rationale=""):
    return {"kind": "relax", "to": to, "rule": rule, "rationale": rationale}

def tighten(to, rule, rationale=""):
    return {"kind": "tighten", "to": to, "rule": rule, "rationale": rationale}

def abstain():
    return None
"""


class PolicyScriptHostUnavailableError(RuntimeError):
    """Raised when a host's runtime dependency is not installed — e.g.
    the Starlark host without the `capabledeputy[starlark]` extra. A
    typed error (Constitution VI fail-closed) so the daemon surfaces a
    clean message instead of an opaque ImportError."""


def _import_starlark() -> Any:
    try:
        import starlark  # optional runtime dep (capabledeputy[starlark]), imported lazily
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise PolicyScriptHostUnavailableError(
            "the Starlark policy host requires the 'starlark' runtime; "
            "install it with `pip install capabledeputy[starlark]` "
            "(starlark-pyo3).",
        ) from e
    return starlark


def _starlark_compile_worker(output: Any, name: str, source: str) -> None:
    """Compile and bind one Starlark policy script in a killable child process."""
    try:
        starlark = _import_starlark()
        ast = starlark.parse(f"<policy:{name}>", _STARLARK_PRELUDE + "\n" + source)
        module = starlark.Module()
        starlark.eval(module, ast, starlark.Globals.standard())
        module.freeze()
        output.put(("ok", None))
    except Exception as exc:
        output.put(("error", str(exc)))


def _starlark_eval_worker(
    output: Any,
    name: str,
    source: str,
    action: dict[str, Any],
    session: dict[str, Any],
    proposed_outcome: dict[str, Any],
) -> None:
    """Evaluate one Starlark policy script in a killable child process."""
    try:
        starlark = _import_starlark()
        ast = starlark.parse(f"<policy:{name}>", _STARLARK_PRELUDE + "\n" + source)
        module = starlark.Module()
        starlark.eval(module, ast, starlark.Globals.standard())
        frozen = module.freeze()
        result = frozen.call("inspect", action, session, proposed_outcome)
        if result is None:
            output.put(("ok", None))
            return
        if not isinstance(result, dict):
            output.put(("non-dict", type(result).__name__))
            return
        output.put(("ok", {str(k): result[k] for k in result}))
    except Exception as exc:
        output.put(("error", str(exc)))


def _run_policy_subprocess(
    target: Any,
    args: tuple[Any, ...],
    *,
    name: str,
    timeout_seconds: float,
) -> tuple[str, Any]:
    start_methods = multiprocessing.get_all_start_methods()
    start_method = next(
        method for method in ("forkserver", "spawn", "fork") if method in start_methods
    )
    ctx = cast(Any, multiprocessing.get_context(start_method))
    output = ctx.Queue(maxsize=1)
    proc = ctx.Process(target=target, args=(output, *args))
    proc.start()
    proc.join(timeout_seconds)
    if proc.is_alive():
        proc.terminate()
        proc.join(0.2)
        if proc.is_alive():
            proc.kill()
            proc.join()
        return (
            "error",
            f"policy script {name!r} exceeded timeout ({timeout_seconds:.3f}s)",
        )
    try:
        status, payload = output.get_nowait()
    except queue.Empty:
        return ("error", f"policy script {name!r} produced no result")
    return str(status), payload


class StarlarkScriptHost:
    """Real sandboxed policy host backed by starlark-rust (via
    starlark-pyo3). Language-level isolation: a script cannot import,
    open files, touch the network, or reach any Python builtin — only
    the injected `action`/`session`/`proposed_outcome` dicts and the
    `relax`/`tighten`/`abstain` helpers are in scope.

    Security boundary (see the threat-model doc):
      - No I/O, no `import`, no host objects — enforced by the Starlark
        language itself, not by best-effort filtering.
      - Deterministic; no clock / randomness inside the script.
      - Residual risk: starlark-pyo3 2026.1 does not expose an in-VM
        step budget, so compile/evaluation run in a killable child
        process with a wall-clock timeout.
    """

    runtime_kind: str = "starlark"

    def __init__(self, *, timeout_seconds: float = 1.0) -> None:
        self._timeout_seconds = timeout_seconds

    def compile(self, name: str, source: str) -> PolicyScript:
        if "def inspect(" not in source:
            raise ValueError(
                f"policy script {name!r} must define `def inspect(...)`",
            )
        _import_starlark()
        status, payload = _run_policy_subprocess(
            _starlark_compile_worker,
            (name, source),
            name=name,
            timeout_seconds=self._timeout_seconds,
        )
        if status != "ok":
            raise ValueError(
                f"policy script {name!r} failed to compile (Starlark): {payload}",
            )
        return PolicyScript(
            name=name,
            source=source,
            runtime_kind=self.runtime_kind,
            timeout_seconds=self._timeout_seconds,
        )

    async def evaluate(
        self,
        script: PolicyScript,
        *,
        action: dict[str, Any],
        session: dict[str, Any],
        proposed_outcome: dict[str, Any],
    ) -> ScriptOutcome:
        from anyio.to_thread import run_sync

        def _evaluate_in_subprocess() -> tuple[str, Any]:
            return _run_policy_subprocess(
                _starlark_eval_worker,
                (script.name, script.source, action, session, proposed_outcome),
                name=script.name,
                timeout_seconds=script.timeout_seconds,
            )

        status, payload = await run_sync(_evaluate_in_subprocess)
        if status == "error":
            return ScriptOutcome(kind="error", error=str(payload))
        if status == "non-dict":
            return ScriptOutcome(
                kind="error",
                error=f"script {script.name!r} returned non-dict: {payload}",
            )

        result = payload
        if result is None:
            return ScriptOutcome(kind="abstain")
        if not isinstance(result, dict):
            return ScriptOutcome(
                kind="error",
                error=f"script {script.name!r} returned non-dict: {type(result).__name__}",
            )
        kind = result.get("kind", "")
        if kind not in ("relax", "tighten"):
            return ScriptOutcome(
                kind="error",
                error=f"script {script.name!r} returned unknown kind {kind!r}",
            )
        return ScriptOutcome(
            kind=kind,
            to_decision=str(result.get("to", "")),
            rule=str(result.get("rule", "")),
            rationale=str(result.get("rationale", "")),
        )


# Host registry — maps a runtime_kind to its implementation. The daemon's
# HostFactory consults this when loading operator policy scripts (the
# script's extension / front-matter selects the runtime). New runtimes
# register here without touching the chokepoint.
_HOST_FACTORIES: dict[str, Any] = {
    SafePythonScriptHost.runtime_kind: SafePythonScriptHost,
    StarlarkScriptHost.runtime_kind: StarlarkScriptHost,
}


def get_script_host(runtime_kind: str) -> PolicyScriptHost:
    """Return a PolicyScriptHost for `runtime_kind` ("starlark" |
    "python-reference"). Fail-closed on an unknown kind (Constitution VI)
    — never silently fall back to the non-sandboxed Python host."""
    factory = _HOST_FACTORIES.get(runtime_kind)
    if factory is None:
        raise ValueError(
            f"unknown policy-script runtime {runtime_kind!r}; known: {sorted(_HOST_FACTORIES)}",
        )
    return factory()
