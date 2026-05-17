#!/usr/bin/env bash
set -euo pipefail

# Install shared git hooks from .githooks/ directory.
# Run once after cloning: ./scripts/setup-hooks.sh

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOKS_DIR="$REPO_ROOT/.githooks"

if [[ ! -d "$HOOKS_DIR" ]]; then
  echo "Error: $HOOKS_DIR not found" >&2
  exit 1
fi

git config core.hooksPath "$HOOKS_DIR"

# Ensure hooks are executable
chmod +x "$HOOKS_DIR"/*

echo "Git hooks installed from .githooks/"
echo "  core.hooksPath = $HOOKS_DIR"
echo ""
echo "Available hooks:"
ls -1 "$HOOKS_DIR"
echo ""
echo "Skip with: git commit --no-verify"
