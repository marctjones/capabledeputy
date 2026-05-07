# Local-model planner

The CapableDeputy daemon picks its LLM through LiteLLM, which already
supports Anthropic, OpenAI, Gemini, **Ollama**, **llama.cpp**, vLLM, and
several other local providers. Switching to a local model is a config
change, not a code change.

## Why run the planner locally

In CapableDeputy two LLMs participate in dual-LLM mode (DESIGN.md §5.2):

- **Planner** — the privileged LLM the agent loop talks to. Sees the
  whole conversation, the tool descriptions, and any non-labeled
  context. In turn-level mode it also sees labeled values directly
  (subject to capability filtering).
- **Quarantined extractor** — sees raw labeled data, has no tools, and
  produces only schema-validated output. Its responses are the
  declassification gate.

If both run on a frontier API, every PHI/financial value the harness
touches in turn-level mode crosses the network. Pinning the planner to
a local model keeps that traffic on-machine; pinning the quarantined
extractor too keeps labeled data on-machine end to end.

## Setup (Ollama)

1. Install [Ollama](https://ollama.ai) and pull a planner-grade model:

   ```bash
   ollama pull llama3.1:70b   # or mistral-nemo:12b for laptops
   ollama pull phi3:mini      # quarantined extractor — small is fine
   ```

2. Point the daemon at them via env vars:

   ```bash
   export CAPDEP_LLM_MODEL="ollama/llama3.1:70b"
   export CAPDEP_QUARANTINED_LLM_MODEL="ollama/phi3:mini"
   capdep daemon start
   ```

   `CAPDEP_LLM_MODEL` is the planner. `CAPDEP_QUARANTINED_LLM_MODEL`
   is optional — if you don't set it the planner LLM is reused, which
   is fine when both run locally but defeats the purpose if the
   planner is a cloud API.

That's it. The agent loop and dual-LLM mode work unchanged; LiteLLM
routes both calls to the local Ollama daemon at `localhost:11434`.

## Config sketch (configs/local-planner.env)

The repo ships an example env file at `configs/local-planner.env`:

```bash
# Planner: the privileged LLM the agent loop drives.
CAPDEP_LLM_MODEL="ollama/llama3.1:70b"

# Quarantined extractor: sees labeled data; produces schema output.
# If unset, the planner LLM is reused.
CAPDEP_QUARANTINED_LLM_MODEL="ollama/phi3:mini"

# Optional: load SKILL.md files from a directory at daemon startup.
# Each skill becomes a labeled tool that calls the quarantined LLM.
CAPDEP_SKILLS_DIR="$HOME/.config/capabledeputy/skills"
```

Source it before launching the daemon:

```bash
source configs/local-planner.env
capdep daemon start
```

## What does and doesn't change

The runtime behaviour is identical regardless of provider. The policy
engine, label propagation, audit log, MCP server surface, and CLI all
work the same way. What changes is purely the data path: with the
local-planner config, no labeled value ever leaves the host.

## Performance notes

- Local planners are slower per token than frontier APIs. Expect 5–20×
  the wall time of a Haiku turn for a large local model.
- The dual-LLM auto-escalation in DESIGN.md §5.4 fires when the session
  has confidential labels AND a quarantined extractor is registered.
  When the planner is local you can keep both LLMs the same model
  (omit `CAPDEP_QUARANTINED_LLM_MODEL`); the extractor still runs in
  no-tools quarantined mode.
- For laptop-scale machines a 7–14B planner with a 1–4B extractor is a
  reasonable starting point.

## Mixed deployment (planner local, extractor frontier)

Acceptable when the labeled data is content you'd send to a cloud API
anyway (e.g., "untrusted external" web content), and you want a
faster/cleaner extraction:

```bash
export CAPDEP_LLM_MODEL="ollama/llama3.1:70b"
export CAPDEP_QUARANTINED_LLM_MODEL="claude-haiku-4-5"
```

Not recommended for `confidential.health` or `confidential.financial`
labeled spaces — those should stay on-machine end to end.

## Mixed deployment (planner frontier, extractor local)

The reverse — frontier planner with local quarantined — is **also
useful**: the planner never sees labeled bytes (because the extractor
gates the declassification), so even a cloud planner on a sensitive
session is safe by construction. Use this when you want the planner's
quality without sending PHI off the box:

```bash
export CAPDEP_LLM_MODEL="claude-opus-4-7"
export CAPDEP_QUARANTINED_LLM_MODEL="ollama/phi3:mini"
```

This relies on the architecture from §5.2: the schema-validated output
of the extractor is the only thing that crosses the boundary, and the
planner sees only typed fields, never raw labeled text.
