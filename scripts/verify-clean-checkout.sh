#!/usr/bin/env bash
# Verify the COMMITTED state the way CI sees it, ignoring uncommitted work.
#
# A dirty working tree can hide defects that only the author's uncommitted edits
# fix (e.g. a `private` type used across files, or an unformatted file). This
# checks out HEAD (or REF) into a throwaway git worktree and runs the fast gates.
#
#   scripts/verify-clean-checkout.sh            # ruff + pyright-free fast gates + swift build
#   scripts/verify-clean-checkout.sh --full     # also run the full pytest + coverage ratchet
#   REF=origin/main scripts/verify-clean-checkout.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REF="${REF:-HEAD}"
FULL=false
[[ "${1:-}" == "--full" ]] && FULL=true

CAPDEP_CACHE_ROOT="${CAPDEP_CACHE_ROOT:-$HOME/Library/Caches/capdep}"
export UV_CACHE_DIR="${CAPDEP_UV_CACHE_DIR:-$CAPDEP_CACHE_ROOT/uv-cache}"
export UV_PYTHON_INSTALL_DIR="${CAPDEP_UV_PYTHON_INSTALL_DIR:-$CAPDEP_CACHE_ROOT/uv-python}"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/capdep-clean-checkout.XXXXXX")"
cleanup() {
  git -C "$REPO_ROOT" worktree remove --force "$WORK/tree" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

git -C "$REPO_ROOT" worktree add --detach "$WORK/tree" "$REF" >/dev/null
echo "verifying $(git -C "$WORK/tree" rev-parse --short HEAD) ($REF) in a clean worktree"
if [[ -n "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=no)" ]]; then
  echo "note: uncommitted changes in your tree are intentionally NOT part of this check"
fi

cd "$WORK/tree"
uv sync --all-groups >/dev/null
echo "== ruff check";        uv run ruff check
echo "== ruff format --check"; uv run ruff format --check
echo "== pyright";           uv run pyright

if [[ -d apps/macos/CapDep ]] && command -v swift >/dev/null 2>&1; then
  echo "== swift build (apps/macos/CapDep)"
  (cd apps/macos/CapDep && swift build)
fi

if $FULL; then
  echo "== pytest"; uv run pytest --cov-report=xml --cov-report=json:coverage.json -q
  echo "== coverage ratchet"; uv run python scripts/coverage_ratchet.py
fi
echo "clean-checkout verification passed for $REF"
