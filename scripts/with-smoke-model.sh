#!/bin/sh
# Apply to the daemon process, not just a client attached to an existing daemon.
set -eu
if [ "$#" -eq 0 ]; then
    echo "Usage: $0 COMMAND [ARG ...]" >&2
    exit 2
fi
CAPDEP_MODELS_CONFIG="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)/configs/models-smoke.yaml"
export CAPDEP_MODELS_CONFIG
export CAPDEP_LLM_BACKEND=mlx
export CAPDEP_LLM_MODEL=mlx/mlx-community/Qwen2.5-0.5B-Instruct-4bit
export CAPDEP_LLM_TOOLS_MODEL="$CAPDEP_LLM_MODEL"
export CAPDEP_LLM_QUALITY_MODEL="$CAPDEP_LLM_MODEL"
export CAPDEP_LLM_CODER_MODEL="$CAPDEP_LLM_MODEL"
export CAPDEP_QUARANTINED_LLM_MODEL="$CAPDEP_LLM_MODEL"
export CAPDEP_MLX_ENABLE_THINKING=0
export HF_HUB_OFFLINE=1
exec "$@"
