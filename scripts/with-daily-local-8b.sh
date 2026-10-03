#!/bin/sh
# Apply to the daemon process, not to a client attached to an existing daemon.
# The model must already be cached; this wrapper deliberately never downloads
# assets or permits a cloud fallback.
set -eu
if [ "$#" -eq 0 ]; then
    echo "Usage: $0 COMMAND [ARG ...]" >&2
    exit 2
fi
REPO_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
CAPDEP_MODELS_CONFIG="$REPO_ROOT/configs/models-daily-local-8b.yaml"
DAILY_MODEL="${CAPDEP_DAILY_MLX_MODEL:-mlx/mlx-community/Qwen3-8B-4bit}"
export CAPDEP_MODELS_CONFIG
export CAPDEP_LLM_BACKEND=mlx
export CAPDEP_LLM_MODEL="$DAILY_MODEL"
export CAPDEP_LLM_TOOLS_MODEL="$CAPDEP_LLM_MODEL"
export CAPDEP_LLM_QUALITY_MODEL="$CAPDEP_LLM_MODEL"
export CAPDEP_LLM_CODER_MODEL="$CAPDEP_LLM_MODEL"
export CAPDEP_QUARANTINED_LLM_MODEL="$CAPDEP_LLM_MODEL"
export CAPDEP_MLX_ENABLE_THINKING=0
export HF_HUB_OFFLINE=1
exec "$@"
