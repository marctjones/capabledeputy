# Model experiment plan

This is the follow-up lane for models that are not current CapDep defaults but
are worth testing because they map to CapDep-specific work:

- tool selection and function calling,
- model-assisted risk annotation,
- retrieval reranking.

The experiment is non-destructive by default. It produces benchmark cases and
conversion commands, but does not download or convert models unless an operator
runs those commands explicitly.

```bash
.venv/bin/python scripts/benchmark_model_experiments.py --check-hf
.venv/bin/python scripts/benchmark_model_experiments.py --purpose tool_selection
.venv/bin/python scripts/benchmark_model_experiments.py --purpose risk_guard
.venv/bin/python scripts/benchmark_model_experiments.py --purpose reranker
```

Results are written to `benchmark-results/model-experiments/plan.jsonl` by
default.

## Candidates

| Purpose | Candidate | Status | Why it is worth testing |
|---|---|---|---|
| Tool selection | `Salesforce/Llama-xLAM-2-8b-fc-r` | Source-convertible with `mlx_lm.convert` | Focused function-calling model that could beat general planners at selecting the right tools. |
| Tool selection | `Salesforce/xLAM-2-32b-fc-r` | Source-convertible with `mlx_lm.convert` | Larger xLAM challenger if 8B is too weak and local latency is acceptable. |
| Risk annotation | `Qwen/Qwen3Guard-Gen-0.6B` | Native MLX exists | Small sidecar for identifying injection, egress, financial, and destructive-action risk signals. |
| Risk annotation | `Qwen/Qwen3Guard-Gen-4B` | Source-convertible with `mlx_lm.convert` | Higher-quality guard sidecar if 0.6B is too weak. |
| Reranking | `BAAI/bge-reranker-v2-m3` | Separate reranker runtime required | Strong default candidate for ranking local docs and retrieved context. |
| Reranking | `jinaai/jina-reranker-v2-base-multilingual` | Separate reranker runtime required | Strong alternative, but license/runtime complexity makes it non-default. |

## Decision rules

- Do not replace the current planner defaults unless a candidate improves the
  specific CapDep task it was chosen for.
- xLAM candidates compete only for tool-surface selection, not general chat.
- Guard candidates annotate risk; they must not become a policy enforcement
  authority. CapDep policy, labels, approvals, and audit remain authoritative.
- Reranker candidates need a dedicated runtime. They should not be forced
  through `mlx_lm.generate`.
- Defaults change only after local benchmark artifacts record latency, output
  validity, and task-specific accuracy.

## Local run, 2026-07-06

The first local pass converted the two smallest source-convertible candidates
and ran deterministic smoke prompts on the M5 laptop.

| Candidate | Artifact | Peak memory | Generation speed | Result |
|---|---:|---:|---:|---|
| `Salesforce/Llama-xLAM-2-8b-fc-r` | 4-bit MLX, 4.2 GB | 4.7 GB | 28-30 tokens/s | Fast and stable, but raw prompting selected only the first necessary tool for multi-step tasks. Keep as a tool-selection experiment, not a default replacement. |
| `Qwen/Qwen3Guard-Gen-4B` | 4-bit MLX, 2.1 GB | 2.8 GB | 11-13 tokens/s | Separates some benign/risky prompts, but emits generic safety categories and missed the financial/destructive approval case. Use only as a possible risk annotation sidecar with CapDep's policy layer remaining authoritative. |

Notes:

- `mlx_lm.convert` needs `--quantize --q-bits 4`; `--q-bits 4` alone produced
  bf16 artifacts. The experiment harness now emits the explicit quantized
  command.
- The 32B xLAM candidate was not converted in this pass. The 8B result does
  not yet justify the larger conversion cost without a better tool-calling
  prompt template or adapter-specific runtime.
- Reranker candidates remain pending because they need a cross-encoder/reranker
  runtime rather than `mlx_lm.generate`.
