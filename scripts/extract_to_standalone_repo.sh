#!/usr/bin/env bash
# Extract sdk/python/ from the VNBrain monorepo into a standalone git repo.
#
# Usage:
#   ./scripts/extract_to_standalone_repo.sh [TARGET_DIR]
#
# Default TARGET_DIR is ../../../talyxion-python (sibling of VNBrain).
# After running, push that directory to a fresh GitHub repo, e.g.:
#   gh repo create talyxion/talyxion-python --public --source=$TARGET_DIR --push
#
# This script does NOT touch the monorepo. The standalone repo gets a fresh
# git history with a single "initial commit". If you want to preserve history
# instead, use `git subtree split --prefix=sdk/python -b sdk-only` from the
# monorepo root.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SDK_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET="${1:-$SDK_DIR/../../../talyxion-python}"

if [[ -e "$TARGET" ]]; then
    echo "Refusing to overwrite existing path: $TARGET" >&2
    echo "Pass a different target dir or remove the existing one first." >&2
    exit 1
fi

echo "==> Copying $SDK_DIR  ->  $TARGET"
mkdir -p "$TARGET"
# Copy everything except build artifacts and venv.
rsync -a \
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
    "$SDK_DIR"/ "$TARGET"/

echo "==> Initialising fresh git repo at $TARGET"
cd "$TARGET"
git init -q -b main
git add .
git -c user.email="sdk@talyxion.com" -c user.name="Talyxion SDK" \
    commit -q -m "chore: initial talyxion python SDK"

echo
echo "Standalone SDK repo ready at: $TARGET"
echo
echo "Next steps:"
echo "  1. cd $TARGET"
echo "  2. Create the GitHub repo (private or public):"
echo "       gh repo create talyxion/talyxion-python --public --source=. --remote=origin --push"
echo "  3. Set up PyPI Trusted Publisher (see docs/PUBLISHING.md)."
echo "  4. Create the v0.1.0 tag to trigger the publish workflow:"
echo "       git tag v0.1.0 && git push origin v0.1.0"
