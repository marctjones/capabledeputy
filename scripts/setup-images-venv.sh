#!/usr/bin/env bash
# Compatibility wrapper for the consolidated setup entry point.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -x "$ROOT/.venv/bin/capdep-setup" ]]; then
  exec "$ROOT/.venv/bin/capdep-setup" images --apply --repo-root "$ROOT" --venv "$ROOT/.venv-images"
fi

exec "$ROOT/.venv/bin/python" -m capabledeputy.cli.setup_cli images --apply --repo-root "$ROOT" --venv "$ROOT/.venv-images"
