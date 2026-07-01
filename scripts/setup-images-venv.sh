#!/usr/bin/env bash
# Isolated Python env for bundled-image-generate (torch + diffusers + transformers<5).
# The main .venv keeps mlx-lm (transformers>=5); image generation cannot share it.
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

echo "[setup-images-venv] verifying torch + images MCP module"
"$PY" -c "import torch; print('torch', torch.__version__)"
"$PY" -c "from capabledeputy.mcp_servers import image_generate; print('tools', [t.name for t in image_generate.tools()])"

echo "[setup-images-venv] syncing daemon config (bundled-image-generate uses this venv)"
"$ROOT/.venv/bin/capdep" setup --no-sandbox 2>/dev/null || "$ROOT/.venv/bin/python" -m capabledeputy.cli.main setup --no-sandbox

echo "[setup-images-venv] done: $PY"