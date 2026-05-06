#!/usr/bin/env bash
# setup-githooks.sh — one-time per-clone wiring of the repo's git hooks.
#
# Sets git's core.hooksPath to .githooks/, so the committed hook scripts
# fire on the standard git events (pre-push, etc). Hooks ship in the repo
# under .githooks/ so all agents share the same gates.
#
# Run this once after `git clone`, or any time .githooks/ contents change
# (chmod +x is reapplied below).
#
# Bypass: any individual hook can be skipped with `--no-verify` on the git
# command (e.g., `git push --no-verify`). Use sparingly; flag in commit msg.

set -euo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"

if [ ! -d .githooks ]; then
    echo "ERROR: .githooks/ directory not found at $REPO_ROOT" >&2
    exit 1
fi

git config core.hooksPath .githooks

# Make all hook scripts executable
chmod +x .githooks/* 2>/dev/null || true

echo "Git hooks configured: core.hooksPath = .githooks"
echo ""
echo "Active hooks:"
ls -la .githooks/ | grep -v '^total' | awk '{print "  " $9 " (" $1 ")"}'
echo ""
echo "Bypass any hook with: git <cmd> --no-verify"
