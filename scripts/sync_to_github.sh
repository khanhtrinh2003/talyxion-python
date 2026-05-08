#!/usr/bin/env bash
# Sync sdk/python/ from the VNBrain monorepo into an existing standalone
# GitHub repo (e.g. https://github.com/khanhtrinh2003/talyxion-python).
#
# Usage:
#   ./scripts/sync_to_github.sh [REPO_URL] [WORKDIR]
#
# Defaults:
#   REPO_URL = https://github.com/khanhtrinh2003/talyxion-python.git
#   WORKDIR  = /tmp/talyxion-python-sync
#
# Workflow:
#   1. Clone REPO_URL into WORKDIR (or reuse if already a clone).
#   2. rsync sdk/python content (excluding build artifacts) into WORKDIR.
#   3. Show `git status` and the diff summary.
#   4. Stop and let YOU run `git commit` and `git push` manually.
#
# This script never pushes for you — pushing is a visible/destructive action,
# so the human stays in the loop.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SDK_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

REPO_URL="${1:-https://github.com/khanhtrinh2003/talyxion-python.git}"
WORKDIR="${2:-/tmp/talyxion-python-sync}"

if [[ -d "$WORKDIR/.git" ]]; then
    echo "==> Reusing existing clone at $WORKDIR"
    git -C "$WORKDIR" fetch origin
    git -C "$WORKDIR" checkout main
    git -C "$WORKDIR" reset --hard origin/main
else
    echo "==> Cloning $REPO_URL  ->  $WORKDIR"
    rm -rf "$WORKDIR"
    git clone "$REPO_URL" "$WORKDIR"
fi

echo "==> Syncing $SDK_DIR  ->  $WORKDIR"
rsync -a --delete \
    --exclude '.git' \
    --exclude '.venv' \
    --exclude 'venv' \
    --exclude '__pycache__' \
    --exclude '*.egg-info' \
    --exclude 'build' \
    --exclude 'dist' \
    --exclude '.pytest_cache' \
    --exclude '.mypy_cache' \
    --exclude '.ruff_cache' \
    --exclude 'htmlcov' \
    --exclude '.coverage' \
    "$SDK_DIR"/ "$WORKDIR"/

cd "$WORKDIR"

echo
echo "==> Git status after sync:"
git status --short
echo
echo "==> Files changed:"
git diff --stat HEAD || true

echo
if git diff --quiet && git diff --cached --quiet && [[ -z "$(git ls-files --others --exclude-standard)" ]]; then
    echo "Nothing to commit — repo is already in sync."
    exit 0
fi

echo "================================================================"
echo "Repo prepared at: $WORKDIR"
echo
echo "Next steps (run manually so you stay in control of the push):"
echo
echo "  cd $WORKDIR"
echo "  git add -A"
echo "  git commit -m 'release: v\$(grep -oE \"[0-9.]+\" src/talyxion/_version.py | head -1)'"
echo "  git push origin main"
echo
echo "Then to publish to PyPI:"
echo "  git tag v\$(grep -oE \"[0-9.]+\" src/talyxion/_version.py | head -1)"
echo "  git push origin --tags"
echo "================================================================"
