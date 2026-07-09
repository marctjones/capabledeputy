#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ -x "$REPO_ROOT/.venv/bin/uv" ]]; then
  UV="$REPO_ROOT/.venv/bin/uv"
elif command -v uv >/dev/null 2>&1; then
  UV="$(command -v uv)"
else
  echo "uv is required. Install it once with: brew install uv" >&2
  exit 1
fi

"$UV" sync --all-groups

if [[ ! -x "$REPO_ROOT/.venv/bin/capdep" ]]; then
  echo "expected .venv/bin/capdep after uv sync" >&2
  exit 1
fi

if [[ ! -x "$REPO_ROOT/.venv/bin/uvx" ]]; then
  echo "expected .venv/bin/uvx after uv sync" >&2
  exit 1
fi

cat <<EOF
CapDep dev environment is ready.

Use this environment consistently:
  export PATH="$REPO_ROOT/.venv/bin:\$PATH"
  uv run pytest
  apps/macos/CapDep/scripts/run-local-app.sh

The daemon launch helpers put .venv/bin first on PATH automatically.
EOF
