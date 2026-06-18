# LLM Backends

CapDep's planning LLM is pluggable behind the `LLMClient` protocol. Select a
backend with `CAPDEP_LLM_BACKEND`, or leave it unset for the platform default.

| Backend | `CAPDEP_LLM_BACKEND` | Auth / billing | Use for |
|---|---|---|---|
| MLX | unset on Apple Silicon macOS, or `mlx` | local runtime | Apple Silicon default |
| LiteLLM | `litellm` | `ANTHROPIC_API_KEY`, per-token | hosted / multi-user / production |
| Claude CLI | `claude-cli` | logged-in Claude CLI subscription | subscriber's own local use |

## MLX Local Default

On Apple Silicon macOS, CapDep defaults to:

```bash
export CAPDEP_LLM_MODEL=mlx/Qwen/Qwen3-4B-MLX-4bit
```

You can choose another MLX model with either form:

```bash
export CAPDEP_LLM_MODEL=mlx/Qwen/Qwen3-4B-MLX-4bit
export CAPDEP_LLM_BACKEND=mlx
export CAPDEP_LLM_MODEL=Qwen/Qwen3-4B-MLX-4bit
```

Thinking output is stripped before parsing/rendering. Model-native thinking is
off by default for stricter JSON/tool-call envelopes; enable it explicitly:

```bash
export CAPDEP_MLX_ENABLE_THINKING=1
```

## LiteLLM API Backend

```bash
export CAPDEP_LLM_BACKEND=litellm
export ANTHROPIC_API_KEY=sk-ant-...
export CAPDEP_LLM_MODEL=claude-haiku-4-5
```

Calls `litellm.acompletion(...)`; billed per token. This is the right backend
for hosted, shared, or production deployments.

## Claude CLI Backend

```bash
export CAPDEP_LLM_BACKEND=claude-cli
export CAPDEP_CLAUDE_MODEL=haiku
# export CAPDEP_CLAUDE_BIN=claude
```

CapDep shells out to `claude -p --output-format json` using the locally
logged-in Claude CLI account. It is intended only for the subscriber's own
local use. Do not route other users' requests through subscription
credentials; use the API backend for hosted or multi-user deployments.

The safety invariant is load-bearing: CapDep invokes the CLI with every
built-in Claude Code tool disabled and one turn only. The planner can propose a
tool call as JSON, but CapDep remains the only component that gates and
executes tools. Keep `llm/claude_cli.py:DISABLED_TOOLS` current if Claude adds
built-ins.

Tradeoffs:

- The CLI returns a text completion, so CapDep prompts for a structured
  tool-call JSON and parses it. This is slightly less reliable than the API's
  native tool-use.
- Each call spawns a `claude` subprocess, so latency is higher than a direct
  API call.
- The quarantined-extractor LLM (`CAPDEP_QUARANTINED_LLM_MODEL`) still uses
  the selected CapDep LLM factory unless explicitly configured.
