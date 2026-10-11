#!/usr/bin/env bash
# Run CapableDeputy's layered validation without mixing test data and real data.
#
# Profiles intentionally progress from deterministic checks to opt-in host work:
#   core   -- the Python CI gate (default)
#   macos  -- core plus Swift package/app-shell build checks
#   live   -- macos plus the locally configured daemon and GUI parity probe
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# A workstation may have a protected shared uv cache (for example when the
# daemon is launched from a sandboxed IDE). Keep test dependency resolution
# isolated and overridable rather than failing before the first check runs.
#
# Defaults live under ~/Library/Caches (not /tmp): macOS purges /tmp, which used
# to strip the managed interpreter out from under .venv and break every run.
CAPDEP_CACHE_ROOT="${CAPDEP_CACHE_ROOT:-$HOME/Library/Caches/capdep}"
export UV_CACHE_DIR="${CAPDEP_UV_CACHE_DIR:-$CAPDEP_CACHE_ROOT/uv-cache}"
export UV_PYTHON_INSTALL_DIR="${CAPDEP_UV_PYTHON_INSTALL_DIR:-$CAPDEP_CACHE_ROOT/uv-python}"

VENV="$REPO_ROOT/.venv"
# A venv whose interpreter cannot import `encodings` is broken (purged or moved
# base interpreter). Fail fast with a repair hint instead of a cryptic abort.
if [[ -x "$VENV/bin/python" ]] && ! "$VENV/bin/python" -c 'import encodings' >/dev/null 2>&1; then
  echo "error: $VENV/bin/python is broken (its base interpreter is missing)." >&2
  echo "repair: rm -rf '$VENV' && scripts/bootstrap-dev-env.sh" >&2
  exit 1
fi
if [[ -x "$VENV/bin/python" ]]; then
  RUN_FROM_VENV=true
elif [[ -x "$REPO_ROOT/.venv/bin/uv" ]]; then
  RUN_FROM_VENV=false
  UV="$REPO_ROOT/.venv/bin/uv"
elif command -v uv >/dev/null 2>&1; then
  RUN_FROM_VENV=false
  UV="$(command -v uv)"
else
  echo "uv is required; run scripts/bootstrap-dev-env.sh first." >&2
  exit 1
fi

usage() {
  cat <<'USAGE'
usage: scripts/test-capdep.sh [core|macos|live]

  core   Deterministic Python CI gate: ruff, format, pyright, pytest, coverage ratchet.
  macos  core plus Swift package tests and an unsigned CapDepMac app-shell build.
  live   macos plus local daily-driver readiness and daemon/GUI parity. Starts the
         user LaunchAgent if it is stopped; it never sends mail or mutates an account.

The default is core. Real credentials, real model inference, and GUI interaction
are opt-in through the live profile; all irreversible test cases stay simulated.
USAGE
}

profile="${1:-core}"
case "$profile" in
  core|macos|live) ;;
  -h|--help)
    usage
    exit 0
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac

run_core() {
  if [[ "$RUN_FROM_VENV" == true ]]; then
    "$VENV/bin/ruff" check
    "$VENV/bin/ruff" format --check
    "$VENV/bin/pyright"
    "$VENV/bin/python" -m pytest --cov-report=xml
    "$VENV/bin/python" scripts/coverage_ratchet.py
  else
    "$UV" run ruff check
    "$UV" run ruff format --check
    "$UV" run pyright
    "$UV" run pytest --cov-report=xml
    "$UV" run python scripts/coverage_ratchet.py
  fi
}

run_macos() {
  (
    cd apps/macos/CapDep
    swift test --enable-code-coverage
  )
  .venv/bin/python scripts/swift_coverage_summary.py --limit 20
  (
    cd apps/macos/CapDep/App
    xcodebuild -project CapDep.xcodeproj -scheme CapDep \
      -configuration Debug -derivedDataPath .derived \
      build CODE_SIGNING_ALLOWED=NO
  )
}

run_live() {
  .venv/bin/capdep doctor
  .venv/bin/capdep-setup daily-driver --json
  scripts/run-local-daemon-launchd.sh start
  .venv/bin/python scripts/verify-gui-parity.py
}

run_core
if [[ "$profile" == "macos" || "$profile" == "live" ]]; then
  run_macos
fi
if [[ "$profile" == "live" ]]; then
  run_live
fi

echo "CapableDeputy $profile validation passed."
