# LLM backends

capdep's planning LLM is pluggable behind the `LLMClient` protocol. Select the
backend with `CAPDEP_LLM_BACKEND`.

| Backend | `CAPDEP_LLM_BACKEND` | Auth / billing | Use for |
|---|---|---|---|
| **LiteLLM** (default) | unset / `litellm` | `ANTHROPIC_API_KEY`, per-token | hosted / multi-user / production — the sanctioned path |
| **Claude CLI** | `claude-cli` | your logged-in Claude **subscription** (`claude -p` agent-pool credits) | the subscriber's **own local use** (no API key needed) |

## Default — LiteLLM → the Anthropic API

```bash
export ANTHROPIC_API_KEY=sk-ant-…
export CAPDEP_LLM_MODEL=claude-haiku-4-5     # optional; this is the default
```

Calls `litellm.acompletion(...)`; billed per token. This is the right backend
for anything serving more than just you.

## Claude CLI → your subscription (local use)

```bash
export CAPDEP_LLM_BACKEND=claude-cli
export CAPDEP_CLAUDE_MODEL=haiku            # optional: a `claude` alias (haiku/sonnet/opus) or full id
# CAPDEP_CLAUDE_BIN=claude                  # optional: path to the claude binary
```

capdep shells out to `claude -p --output-format json` using whatever account
you're logged into (`claude /login`). If that's a Pro/Max subscription, it draws
on the subscription's Agent-SDK credit pool — **no API key, no per-token bill**.

### What's allowed (and what isn't)

- ✅ **You, the subscriber, using capdep locally.** As of June 2026 Anthropic
  permits `claude -p` / the Agent SDK to use *your own* subscription credits.
- ❌ **A hosted / multi-user backend.** Routing other users' requests through
  subscription credentials violates Anthropic's terms — use the API (LiteLLM)
  there. Do **not** extract or proxy subscription OAuth tokens.

### The safety invariant (load-bearing)

capdep's whole job is to mediate the agent's tool calls. So the `claude` CLI is
invoked with **every built-in tool disabled** (`--disallowed-tools Read Write
Edit Bash Glob Grep WebFetch WebSearch …`) and a single turn. The planner can
only *propose* a tool call as JSON; capdep's engine then gates and executes it.
If Claude Code's own tools were left enabled, it would read files / run bash /
fetch the web **behind the policy gate** — which would defeat capdep. The list
lives in `llm/claude_cli.py:DISABLED_TOOLS`; keep it current if Anthropic adds
built-in tools.

### Tradeoffs

- The CLI returns a *text* completion, so capdep prompts for a structured
  tool-call JSON and parses it — slightly less reliable than the API's native
  tool-use, but works well in practice.
- Each call spawns a `claude` subprocess (higher per-call latency than a direct
  API call).
- The quarantined-extractor LLM (`CAPDEP_QUARANTINED_LLM_MODEL`) still uses
  LiteLLM; set it only if you have API access.
