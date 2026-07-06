# MLX model candidate validation

Validated: 2026-07-06.

This note compares three practical Apple Silicon model candidates for each
CapDep runtime purpose. The shortlist intentionally prefers already-native
MLX, MLX-VLM, or MFLUX artifacts. Source-only PyTorch, GGUF, GPTQ, AWQ, and
diffusers candidates are not listed as top options when a native MLX/MFLUX
artifact is already available, because CapDep's setup path should avoid
unnecessary conversion, provenance, and compatibility risk.

Validation checks performed:

- Hugging Face repository exists and is readable.
- Repository is not private.
- Repository advertises MLX, MLX-VLM-compatible, or MFLUX-compatible tags.
- Gate/license state is recorded.
- Local cache state is recorded separately from recommendation quality.

## Summary recommendation

| Purpose | Default recommendation | Backup | Conservative fallback |
|---|---|---|---|
| Fast/default chat | `Qwen/Qwen3-4B-MLX-4bit` | `mlx-community/Qwen3-8B-4bit` | `mlx-community/Qwen3-1.7B-4bit` |
| Tool-heavy planning | `mlx-community/Qwen3-14B-4bit` | `mlx-community/Qwen3-8B-4bit` | `lmstudio-community/Mistral-Small-3.2-24B-Instruct-2506-MLX-4bit` |
| Quality planning | `mlx-community/Qwen3-30B-A3B-4bit` | `lmstudio-community/Mistral-Small-3.2-24B-Instruct-2506-MLX-4bit` | `mlx-community/Qwen3-32B-4bit` |
| Coding/scripting | `mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit` | `mlx-community/Qwen2.5-Coder-14B-Instruct-4bit` | `mlx-community/Devstral-Small-2507-4bit` |
| Quarantined extraction | `mlx-community/Phi-3.5-mini-instruct-4bit` | `mlx-community/Qwen3-1.7B-4bit` | `mlx-community/Llama-3.2-3B-Instruct-4bit` |
| Vision-language | `mlx-community/Qwen2.5-VL-7B-Instruct-4bit` | `mlx-community/InternVL3-8B-6bit` | `mlx-community/Qwen2.5-VL-32B-Instruct-4bit` |
| Image generation | `filipstrand/Z-Image-Turbo-mflux-4bit` | `black-forest-labs/FLUX.1-schnell` via MFLUX | `dhairyashil/FLUX.1-schnell-mflux-4bit` |

## Fast/default chat

| Candidate | MLX status | Gate | License | Local cache | Fit |
|---|---:|---:|---:|---:|---|
| `Qwen/Qwen3-4B-MLX-4bit` | Native MLX 4-bit | No | Apache-2.0 | Missing | Best default balance for normal chat and short turns. |
| `mlx-community/Qwen3-8B-4bit` | Native MLX 4-bit | No | Apache-2.0 | Missing | Better instruction-following reserve when 4B underperforms. |
| `mlx-community/Qwen3-1.7B-4bit` | Native MLX 4-bit | No | Apache-2.0 | Missing | Ultra-fast fallback for very small prompts and smoke tests. |

Decision: keep `Qwen/Qwen3-4B-MLX-4bit` as the default fast planner. Promote
8B only if benchmarked latency on the target machine is still acceptable.

## Tool-heavy planning

| Candidate | MLX status | Gate | License | Local cache | Fit |
|---|---:|---:|---:|---:|---|
| `mlx-community/Qwen3-14B-4bit` | Native MLX 4-bit | No | Apache-2.0 | Cached | Current best fit for tool selection, image/tool routing, and richer turns. |
| `mlx-community/Qwen3-8B-4bit` | Native MLX 4-bit | No | Apache-2.0 | Missing | Lower-latency option if 14B is too slow. |
| `lmstudio-community/Mistral-Small-3.2-24B-Instruct-2506-MLX-4bit` | Native MLX 4-bit | No | Apache-2.0 | Missing | Strong general planner, but needs CapDep-specific tool-use smoke tests. |

Decision: keep `mlx-community/Qwen3-14B-4bit` as the tool-heavy planner until a
local benchmark shows Mistral Small gives materially better tool decisions.

## Quality planning

| Candidate | MLX status | Gate | License | Local cache | Fit |
|---|---:|---:|---:|---:|---|
| `mlx-community/Qwen3-30B-A3B-4bit` | Native MLX 4-bit MoE | No | Apache-2.0 | Missing | Best default quality candidate for long reasoning without full dense 30B cost. |
| `lmstudio-community/Mistral-Small-3.2-24B-Instruct-2506-MLX-4bit` | Native MLX 4-bit | No | Apache-2.0 | Missing | Strong prose/planning alternative; less aligned with current Qwen routing. |
| `mlx-community/Qwen3-32B-4bit` | Native MLX 4-bit dense | No | Apache-2.0 | Missing | Useful quality baseline, but likely heavier than 30B-A3B for similar benefit. |

Decision: keep `mlx-community/Qwen3-30B-A3B-4bit` as the quality role and use
Mistral Small as the first A/B challenger.

## Coding and safe scripting

| Candidate | MLX status | Gate | License | Local cache | Fit |
|---|---:|---:|---:|---:|---|
| `mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit` | Native MLX 4-bit MoE | No | Apache-2.0 | Missing | Best fit for CapDep's professional scripting assistant goal. |
| `mlx-community/Qwen2.5-Coder-14B-Instruct-4bit` | Native MLX 4-bit | No | Apache-2.0 | Missing | Smaller, proven coding fallback for simpler scripts. |
| `mlx-community/Devstral-Small-2507-4bit` | Native MLX 4-bit | No | Apache-2.0 | Missing | Agentic coding alternative; needs sandbox/tool-flow evaluation. |

Decision: use `mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit` for the coder
role when available, with Qwen2.5 Coder 14B as the practical fallback.

## Quarantined extraction

| Candidate | MLX status | Gate | License | Local cache | Fit |
|---|---:|---:|---:|---:|---|
| `mlx-community/Phi-3.5-mini-instruct-4bit` | Native MLX 4-bit | No | MIT | Missing | Best small extraction model for bounded schema tasks. |
| `mlx-community/Qwen3-1.7B-4bit` | Native MLX 4-bit | No | Apache-2.0 | Missing | Very small Qwen-family fallback; likely faster but less capable. |
| `mlx-community/Llama-3.2-3B-Instruct-4bit` | Native MLX 4-bit | No | Llama 3.2 | Missing | Strong small fallback, but license is less clean than Apache/MIT. |

Decision: keep `mlx-community/Phi-3.5-mini-instruct-4bit` for extraction. It is
small, separate from the planner family, and has a permissive MIT license.

## Vision-language

| Candidate | MLX status | Gate | License | Local cache | Fit |
|---|---:|---:|---:|---:|---|
| `mlx-community/Qwen2.5-VL-7B-Instruct-4bit` | Native MLX-VLM 4-bit | No | Apache-2.0 | Missing | Best first VLM for image inspection and generated-image session grounding. |
| `mlx-community/InternVL3-8B-6bit` | Native MLX-VLM 6-bit | No | Other | Missing | Useful alternate architecture; custom-code/license risk is higher. |
| `mlx-community/Qwen2.5-VL-32B-Instruct-4bit` | Native MLX-VLM 4-bit | No | Apache-2.0 | Missing | Higher quality candidate, but probably too heavy for default laptop use. |

Decision: prefer `mlx-community/Qwen2.5-VL-7B-Instruct-4bit` when CapDep adds an
explicit VLM runtime path. Do not route this through `mlx-lm` text planner code.

## Image generation

| Candidate | MLX/MFLUX status | Gate | License | Local cache | Fit |
|---|---:|---:|---:|---:|---|
| `filipstrand/Z-Image-Turbo-mflux-4bit` | Native MFLUX 4-bit | No | Other | Cached | Best interactive default due to existing local cache and MFLUX packaging. |
| `black-forest-labs/FLUX.1-schnell` | MFLUX runtime-native family | Auto | Apache-2.0 | Cached | Best supported FLUX baseline; gate must remain explicit. |
| `dhairyashil/FLUX.1-schnell-mflux-4bit` | Native MFLUX 4-bit | No | Apache-2.0 | Missing | Ungated MFLUX-packaged fallback, but lower adoption signal. |

Decision: keep `filipstrand/Z-Image-Turbo-mflux-4bit` as the interactive image
default and keep `FLUX.1-schnell` as the benchmark/control family.

## Conversion policy

For this shortlist, "converted to MLX" means one of:

- The repository is already a native MLX text model.
- The repository is already a native MLX-VLM model and must run through an
  explicit VLM backend.
- The repository is already MFLUX-compatible for image generation.
- The model family is runtime-native in MFLUX and does not need a local
  `mlx_lm.convert` artifact.

Do not spend local setup time converting source checkpoints for these purposes
until a candidate lacks a native artifact or benchmark evidence shows that a
repo-local conversion is materially better than the published MLX/MFLUX asset.

