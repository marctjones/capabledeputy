# Fireworks.ai as an alternative to local planner models — investigation

Status: **investigation, 2026-10-10**. No code path sends data to Fireworks yet; no API key was used.
Vendor facts below come from Fireworks' public documentation and third-party summaries and **must be re-verified against
the Trust Center / contract before any real data is sent** (marked ⚠ where only a secondary source was found).

## Why consider it

The local MLX planners are the default for privacy, but the tracker shows their cost: host memory pressure blocks the
reproducible email path (#507), larger roles (`planner.quality` 30B, `planner.coder` 30B) strain 16–32 GB Macs, and
model choice is still unsettled (#338, #496). A hosted open-weight model could give faster, larger-model turns for work that
does not involve sensitive content.

## What Fireworks offers (facts)

| Topic | Finding | Source |
|---|---|---|
| API | OpenAI-compatible chat completions with a `tools` array; `https://api.fireworks.ai/inference/v1` | [LlamaIndex cookbook](https://developers.llamaindex.ai/python/framework/integrations/llm/fireworks_cookbook/index.md), [LiteLLM docs](https://docs.litellm.ai/docs/providers/fireworks_ai) |
| LiteLLM | `fireworks_ai/` prefix + `FIREWORKS_AI_API_KEY`; use full `accounts/fireworks/models/<id>` paths (short names get rewritten and can break router endpoints) | [LiteLLM](https://docs.litellm.ai/docs/providers/fireworks_ai), [Fireworks guide](https://docs.fireworks.ai/ecosystem/integrations/litellm) |
| Data retention | Zero data retention by default for open models; prompt/generation content not written to persistent storage; operational metadata (e.g. token counts) is logged | [Fireworks data handling](https://docs.fireworks.ai/guides/security_compliance/data_handling) |
| Caveats | Prompt caching can hold prompt data + KV caches in volatile memory for minutes; the Responses API has its own retention policy (⚠ a secondary source says 30 days with `store=True`); ZDR wording covers *open* models | same; [summary](https://meetily.ai/llm-privacy/fireworks) |
| Training | Does not train on API inputs without opt-in | [data security](https://docs.fireworks.ai/guides/security_compliance/data_security) |
| Compliance | SOC 2 Type II and HIPAA stated; ISO 27001/27701/42001 in progress; audit reports via Trust Center | [Fireworks blog](https://fireworks.ai/blog/fireworks-ai-achieves-soc-2-type-ii-and-hipaa-compliance), [FAQ](https://docs.fireworks.ai/faq-new/security-compliance/what-type-of-certifications-do-you-have) |
| Pricing | Per token (input / cached input / output); batch 50% off; $1 free credit. ⚠ Per-model rates were seen only on third-party pages | [Fireworks pricing docs](https://docs.fireworks.ai/serverless/pricing.md) |
| Tool-calling models | Model catalog has a function-calling filter; Qwen3 support not confirmed by the sources found — check the catalog | [catalog filter via cookbook](https://developers.llamaindex.ai/python/framework/integrations/llm/fireworks_cookbook/index.md) |

## What CapableDeputy already provides

- `LiteLLMClient` (`src/capabledeputy/llm/litellm_client.py`) already speaks the OpenAI tool shape and sanitises dotted tool
  names, so Fireworks works *mechanically* with `CAPDEP_LLM_BACKEND=litellm`, `CAPDEP_LLM_MODEL=fireworks_ai/accounts/fireworks/models/<id>`,
  `FIREWORKS_AI_API_KEY` in the environment, plus `CAPDEP_ALLOW_REMOTE_LLM=1`.
- A coarse, global opt-in exists (`CAPDEP_ALLOW_REMOTE_LLM`).

## Gaps found in this investigation

1. **The local-only guard was a denylist (fixed in this branch).** `require_mlx_on_apple_silicon` only refused `ollama/` and names
   containing "claude"; a `fireworks_ai/…` or `openai/…` spec passed without the opt-in. It is now an allowlist
   (`is_local_model_spec`), with tests in `tests/test_require_mlx_guard.py`.
2. **The opt-in is not label-aware.** Once enabled, *every* turn's planner context goes to the hosted model, including mail bodies and
   documents the user labelled sensitive. The information-flow model treats the planner as untrusted for *integrity* (it cannot act without
   `decide()`), but a hosted planner is also a **confidentiality egress edge**: Denning/Bell–LaPadula-style reasoning says content
   above the destination's clearance must not flow there. Today nothing models the planner endpoint as a destination with a clearance.
3. **No per-role routing to a hosted model.** `models.yaml` roles are MLX-only (`ModelRoutingResult.mlx_model`).
4. **Secrets handling.** The key must come from the environment/Keychain, never from a cwd file (see the cwd-relative key issue in the tracker).

## Recommendation

Treat Fireworks as an **opt-in, label-gated, per-role hosted planner**, not a replacement for local models:

1. *Planner clearance.* Give every planner endpoint a clearance (`local` or `hosted:<provider>`), default `local`. Before a turn is sent to a
   hosted endpoint, compute the session's label join; if it includes any sensitive/restricted tier (or the user's `never_leaves_device` purposes),
   route to a local role instead or ask the user for an explicit, per-session "send this conversation to a hosted model" approval that names the provider.
2. *Quarantined extraction stays local.* The extractor that reads raw untrusted content (Phi-class) keeps running locally; only its
   schema-bounded output and non-sensitive context may reach a hosted planner.
3. *Honest UI.* The status line shows "Planner: hosted (Fireworks) — not on this Mac" whenever a hosted model is active; never "private".
4. *Evaluate before adopting.* Run the held-out workflow set (#496) against 1–2 Fireworks function-calling models and record quality,
   cold/warm latency and cost next to the MLX roles (#338, #348). Adopt a hosted role only if it beats local on the non-sensitive workflows.
5. *Legal/ops checks first.* Verify ZDR scope for the chosen model, Responses-API retention (avoid that endpoint), and obtain the Trust Center
   report; do not send real mail/document content until then.

## Proposed work

- Spike: hosted-planner evaluation harness + Fireworks adapter (thin LiteLLM alias, model-ID validation, key via Keychain/env).
- Feature: planner clearance + label-gated routing + approval + UI disclosure.
- Test: offline tests with a mocked `litellm.acompletion` asserting that (a) sensitive-labelled contexts never reach a hosted client, (b) the
  guard refuses hosted specs without opt-in (done), (c) the hosted indicator appears.
