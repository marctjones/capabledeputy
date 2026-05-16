# Demo 13: Tool-Token Aliasing (Strict Object-Capability)

**Audience:** anyone asking how strict the capability model gets.
**Time:** ~2 minutes.
**Requires:** nothing beyond `uv sync`.

Per-session unforgeable tokens are the strict-ocap defense-in-depth
on top of the existing visibility filter. A session created with
`tool_aliasing=True` shows the LLM session-specific token names like
`t_8c3f1a2b` instead of canonical names like `memory.read`. The LLM
literally cannot reference a tool whose token it doesn't know.

## What the demo proves

1. With aliasing on, the LLM-visible tool list uses tokens.
2. The harness reverse-maps the token to the canonical name at
   dispatch.
3. **A token from session A is meaningless in session B** — the hash
   is keyed by `(session_id, tool_name)`, so cross-session token
   reuse fails with a `tool not found` denial.
4. Aliasing is **opt-in**; default behaviour is unchanged.

## Walkthrough

```bash
uv run pytest tests/test_e2e_tool_aliasing.py -v
```

### The token

```python
from capabledeputy.tools.aliasing import alias_for

alias_for(session.id, "memory.read")
# → "t_8c3f1a2b"   (deterministic per session+tool)
```

The token is `"t_" + sha256(session_id || tool_name)[:8]`. Same
session + same tool → same token (audit logs replay deterministically).
Different session → different token (a leaked token is meaningless
elsewhere).

### Cross-session replay fails

```python
a = alias_for(session_a.id, "memory.read")
b = alias_for(session_b.id, "memory.read")
assert a != b

# Use A's token in B's session:
result = await handlers["session.send"]({"session_id": str(b.id), ...})
# tool_call.name = a's token
# → outcome.decision == "deny"
# → outcome.reason includes "not found"
```

### Real-LLM performance comparison

The integration test `tests/integration/test_real_llm_aliasing.py`
runs the prescription scenario against `claude-haiku-4-5` with
aliasing on vs off and compares iterations + outcomes. Both runs
take 2 iterations and produce identical decisions; opaque token
names don't measurably hurt model performance.

## How to enable

```bash
capdep session new --tool-tokens --intent "strict ocap session"
```

The flag persists with the session (schema v2 in SQLite). All future
turns route their tool list through `alias_for`.

## Files

- `src/capabledeputy/tools/aliasing.py`
- `src/capabledeputy/agent/loop.py` — `build_tool_descriptions` +
  reverse-map at dispatch
- `tests/test_e2e_tool_aliasing.py`
