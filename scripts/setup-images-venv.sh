#!/usr/bin/env bash
# Isolated Python env for bundled-image-generate.
# Apple Silicon prefers MFLUX/MLX; Diffusers SDXL remains a fallback.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/.venv-images"
PY="$VENV/bin/python"

if [[ ! -x "$PY" ]]; then
  echo "[setup-images-venv] creating $VENV"
  python3 -m venv "$VENV"
fi

echo "[setup-images-venv] installing capabledeputy (no deps) + image stack into $VENV"
"$PY" -m pip install -U pip wheel
"$PY" -m pip install -e "$ROOT" --no-deps
if [[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]]; then
  "$PY" -m pip install \
    "mflux>=0.18.0" \
    "torch>=2.7.1" \
    "diffusers>=0.30" \
    "transformers>=5" \
    "accelerate>=0.33" \
    "safetensors>=0.4" \
    "httpx>=0.28" \
    "anyio>=4.4" \
    "mcp>=1.0" \
    "pyyaml>=6.0"
else
  "$PY" -m pip install \
  "torch>=2.2" \
  "diffusers>=0.30" \
  "transformers>=4.44,<5" \
  "accelerate>=0.33" \
  "safetensors>=0.4" \
  "httpx>=0.28" \
  "anyio>=4.4" \
  "mcp>=1.0" \
  "pyyaml>=6.0"
fi

echo "[setup-images-venv] verifying image runtime + images MCP module"
"$PY" -c "import torch; print('torch', torch.__version__)"
"$PY" -c "import importlib.util; print('mflux', 'installed' if importlib.util.find_spec('mflux') else 'missing')"
"$PY" -c "from capabledeputy.mcp_servers import image_generate; print('tools', [t.name for t in image_generate.tools()])"

echo "[setup-images-venv] syncing daemon config (bundled-image-generate uses this venv)"
"$ROOT/.venv/bin/capdep" setup --no-sandbox 2>/dev/null || "$ROOT/.venv/bin/python" -m capabledeputy.cli.main setup --no-sandbox

echo "[setup-images-venv] done: $PY"
