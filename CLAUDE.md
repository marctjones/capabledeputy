<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan:
`specs/003-labeling-framework/plan.md`
<!-- SPECKIT END -->

## Loading HF models

If/when memsafe needs to load HuggingFace models, use the shared model pool
convention (env var `AI_MODELS_DIR`, default `~/Projects/aishared/models/`).
Pre-fetch with `ai-fetch <hf-id>` (bash function in `~/.bashrc`), then
load from `$AI_MODELS_DIR/<org>_<model>/` — never load by HF model ID
(that fills `~/.cache/huggingface/` instead of the shared pool). Full
loader pattern in `~/.claude/skills/aishared-resources/SKILL.md`.
