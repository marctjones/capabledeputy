# Local-model planner

The CapableDeputy daemon picks its LLM through a backend factory. Hosted
and API-backed providers still go through LiteLLM; Apple Silicon local
inference goes through MLX; Ollama, llama.cpp, vLLM, and other local
providers remain available through explicit model/backend configuration.

On Apple Silicon macOS, CapableDeputy defaults to Apple's MLX runtime
when the operator does not explicitly set `CAPDEP_LLM_MODEL`. The
built-in default model is `Qwen/Qwen3-4B-MLX-4bit`, selected through the
model spec `mlx/Qwen/Qwen3-4B-MLX-4bit`.

## macOS default (MLX)

On Apple Silicon Macs, the daemon prefers MLX unless you explicitly
choose another backend:

```bash
capdep daemon start
```

Equivalent explicit form:

```bash
export CAPDEP_LLM_MODEL="mlx/Qwen/Qwen3-4B-MLX-4bit"
capdep daemon start
```

To opt out, set `CAPDEP_LLM_MODEL` to something else such as
`claude-haiku-4-5` or `ollama/phi4:latest`.

### Optional: enable model thinking mode

By default the MLX adapter disables model-native thinking mode because
CapableDeputy frequently needs strict tool-call JSON or strict
schema-extraction JSON. To enable thinking mode for MLX-backed runs:

```bash
export CAPDEP_MLX_ENABLE_THINKING=1
capdep daemon start
```

When enabled, the adapter strips `<think>...</think>` and
`<thinking>...</thinking>` blocks before CapableDeputy parses tool-call
or extractor output, so structured paths continue to work.

## Native MLX model asset pipeline

`capdep-setup models` is the one-time setup surface for local model assets. It
does not change runtime defaults by itself. The command now emits a
machine-readable inventory for planner, extractor, MFLUX image, and explicit
diffusers fallback profiles:

```bash
capdep-setup models --json
capdep-setup models --apply --download
capdep-setup models --apply --convert
```

The inventory records source repository, source format, recommended runtime,
gate/fallback status, quantization, and conversion feasibility. Existing native
MLX or MFLUX repositories are preferred when they are practical. Supported
conversions write provenance manifests under `CAPDEP_MODEL_ASSET_HOME` or
`$HF_HOME/capdep-model-assets`; each manifest records the profile, source repo,
conversion command, fallback runtime, and placeholders for revision/hash/output
evidence that real conversion jobs can fill in later. Unsupported SDXL/Pony
safetensors remain explicit diffusers fallbacks.

Image readiness and `capdep image profiles` surface the same asset state as
`native`, `converted`, or `source_fallback`. Runtime defaults should only
change after local benchmark evidence justifies the change. Use:

```bash
scripts/benchmark_image_models.py \
  --candidate z-image-turbo \
  --candidate flux2-klein-4b \
  --candidate qwen-image
```

The current three-option shortlist for each CapDep model purpose is tracked in
[`docs/model-candidate-validation.md`](model-candidate-validation.md).

## Recommended MLX roles

The built-in Apple Silicon text roles are:

| Role | Model | Use |
|---|---|---|
| `planner.fast` | `Qwen/Qwen3-4B-MLX-4bit` | Default short turns and normal chat. |
| `planner.tools` | `mlx-community/Qwen3-14B-4bit` | Tool-heavy research/writing turns. |
| `planner.quality` | `mlx-community/Qwen3-30B-A3B-4bit` | Slower quality turns that still use the text `mlx-lm` path. |
| `planner.coder` | `mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit` | Programmatic mode and safe scripting/code generation. |
| `extractor` | `mlx-community/Phi-3.5-mini-instruct-4bit` | Quarantined schema extraction. |

`mlx-community/Qwen3.6-27B-OptiQ-4bit` is tracked as the first
quality-planner challenger. It is downloadable through `capdep-setup models`,
but it remains candidate-only until local CapDep benchmarks show better latency,
memory, and valid-output behavior than the current `planner.quality` default.

`mlx-community/Qwen3.6-35B-A3B-4bit` is tracked as an experimental VLM asset,
not as a default text planner, because it uses the `mlx-vlm` path. Promote it
only after CapDep has an explicit VLM backend and benchmark coverage.

Per-role overrides:

```bash
export CAPDEP_LLM_MODEL="mlx/Qwen/Qwen3-4B-MLX-4bit"
export CAPDEP_LLM_TOOLS_MODEL="mlx/mlx-community/Qwen3-14B-4bit"
export CAPDEP_LLM_QUALITY_MODEL="mlx/mlx-community/Qwen3-30B-A3B-4bit"
export CAPDEP_LLM_CODER_MODEL="mlx/mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit"
```

## Why run the planner locally

In CapableDeputy two LLMs participate in dual-LLM mode (DESIGN.md §5.2):

- **Planner** — the privileged LLM the agent loop talks to. Sees the
  whole conversation, the tool descriptions, and any non-labeled
  context. In turn-level mode it also sees labeled values directly
  (subject to capability filtering).
- **Quarantined extractor** — sees raw labeled data, has no tools, and
  produces only schema-validated output. Its schema-bounded responses are subject to the configured
  declassification policy; schema validity alone does not authorize disclosure.

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

# Optional: load flat SKILL.md files and folder packages at daemon startup.
# Folder packages default to guidance; flat files default to quarantined tools.
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
work the same way. What changes is purely the data path: with both models local, inference does not require network egress. Tool
actions may still send data when explicitly authorized by policy.

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
gates the declassification), but any projected fields sent to a cloud planner still require an
authorized disclosure policy. A schema can constrain output shape without
proving that its contents are safe to disclose. Use this when you want the planner's
quality without sending PHI off the box:

```bash
export CAPDEP_LLM_MODEL="claude-opus-4-7"
export CAPDEP_QUARANTINED_LLM_MODEL="ollama/phi3:mini"
```

This relies on the architecture from §5.2: the schema-validated output
of the extractor is the only thing that crosses the boundary, and the
planner sees only typed fields, never raw labeled text.


## Stabilization runtime contract (2026-09-05)

MLX generation is serialized across roles and sessions. At most one weights
pair remains in the shared model cache; switching models releases the prior
cache entry and clears the Metal allocator cache before loading. Cancellation
stops generation at the next token boundary, not in the middle of a GPU kernel.
A cold model load itself is not interruptible. This favors bounded memory over
parallel generation and may increase role-switch latency.

Existing role defaults remain provisional. On the 24 GB development Mac, use
the 14B tool role as an integration baseline, not a measured winner. Do not
promote a model or download a large challenger until the host has adequate
space and measured workflow evidence. A cached 8B model is available for local
protocol measurements; that does not establish comparative superiority.

The measurement command now runs inference (the default command still writes
only the plan):

```bash
.venv/bin/python scripts/benchmark_model_quality.py --run \
  --model /path/to/cached/mlx-model --repeats 3 \
  --results benchmark-results/model-quality/measured-unique.jsonl
```

This command refuses downloads and refuses to overwrite an existing results
file. It measures synthetic exact-response and tool-selection cases through
CapDep's actual MLX adapter. These are protocol probes, not completed research,
document-editing, or inbox workflows. Model promotion additionally requires
those workflows, cancellation/recovery, and latency under normal desktop load.

### Lightweight infrastructure testing

Use `FakeLLMClient` for deterministic unit/policy tests: no model load or GPU work.
For live MLX adapter testing, download the small instruct model once:

```sh
.venv/bin/hf download mlx-community/Qwen2.5-0.5B-Instruct-4bit
sh scripts/with-smoke-model.sh .venv/bin/capdep daemon start
```

Apply the wrapper to a fresh test daemon process; it cannot reconfigure an
already-running daemon. It changes only its child's environment, forces all five
roles onto the same tiny model, disables thinking, and requires cached assets.
It does not isolate state or connectors: use the existing isolated daemon test
fixtures when testing lifecycle or mutation behavior.
`CAPDEP_MODELS_CONFIG` overrides the model configuration independently of security
configuration. A missing explicit profile fails instead of loading larger defaults.
The smoke profile has 128-token role defaults; explicit per-call limits still
apply (conversational chat currently requests 512). Keep live smoke inputs short.

Measured on this Mac: 3.17 s cold, 0.18–0.41 s warm, 0.40 GB peak MLX allocation.
Six tool probes passed; three exact-text probes failed due to capitalization.
This profile exercises loading, streaming, and parsing, not production quality.
Keep strict expected-output tests and real workflow quality evaluation separate.
See [raw measurements](evidence/model-qwen25-05b-2026-09-05.jsonl).

### Persistent resource watchdog (macOS)

`scripts/guard-daemon-resources.py COMMAND ...` starts an owned process group,
checks it every two seconds, and terminates the group if any of these occur:

- 2,048 MiB aggregate resident memory;
- critical macOS memory pressure immediately, or warning pressure for 15 seconds;
- at least 200% aggregate CPU (two cores) for 30 seconds;
- failure to obtain monitoring telemetry.

It sends TERM, allows five seconds, then sends KILL to remaining group members.
Short CPU bursts are allowed. This is a conservative watchdog, not a hard memory
or GPU allocation limit: sampling can miss spikes, RSS does not capture every GPU
allocation, and the monitor cannot act while macOS is unable to schedule it.
Children that create independent process groups are outside its group accounting.
Global memory pressure still triggers a stop even if another app caused it.

On Marc's Mac the existing LaunchAgent now runs this watchdog at nice priority 10
around `with-smoke-model.sh` and the daemon. KeepAlive is false, so a safety stop
will not trigger an immediate restart loop. Monitoring continues after the Codex
turn ends. Logs: `~/.capdep/resource-guard.log`; original plist backup:
`~/.capdep/backups/daemon-before-resource-guard-*.plist`.
The `run-local-daemon-launchd.sh` start/restart commands preserve an existing
operator-owned plist, including the watchdog and model settings. A missing plist
is generated on first use; `CAPDEP_LAUNCHD_PLIST` can select an explicit location.

Initial live verification: all three chat requests completed (2.53 s cold,
0.50 s warm), peak sampled process-group RSS 584 MiB, peak CPU 51%, and normal
system memory pressure. All GUI RPC checks passed, but the strict parity chat
check failed on capitalization/punctuation (`Parity-ok.`). Unit tests exercise
actual owned-child termination for critical pressure and telemetry failure.
