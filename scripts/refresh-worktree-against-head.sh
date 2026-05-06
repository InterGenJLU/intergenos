#!/usr/bin/env bash
# scripts/refresh-worktree-against-head.sh — restore worktree files when a
# sibling-worktree push has advanced the shared master ref.
#
# Pattern: git worktrees share .git/refs but maintain separate index files.
# When a sibling worktree pushes (advancing refs/heads/master), this
# worktree's index doesn't auto-refresh against the new HEAD. Files added
# in the sibling-pushed commits appear as "phantom staged deletions" at
# `git status` because the index believes those files were "the state
# matching HEAD" — but HEAD now contains them.
#
# Symptoms:
#   $ git status --porcelain
#   D  packages/extra/crun/build.sh
#   D  packages/extra/crun/package.yml
#   ...
# (D in the FIRST column = staged deletion; second column space = working
# tree clean. The files don't exist in the working tree either.)
#
# Fix: refresh the working tree + index against current HEAD for the
# affected paths. This script does that selectively (just the phantom-
# deleted paths) so any intentional uncommitted changes elsewhere are
# preserved.
#
# Usage:
#   bash scripts/refresh-worktree-against-head.sh           # auto-detect + fix
#   bash scripts/refresh-worktree-against-head.sh --dry-run # show what would fix

set -uo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
if [ -z "$REPO_ROOT" ]; then
    echo "ERROR: not in a git repo." >&2
    exit 2
fi
cd "$REPO_ROOT"

DRY_RUN=false
if [ "${1:-}" = "--dry-run" ]; then
    DRY_RUN=true
fi

# Find files that are staged-deleted ('D ' in porcelain output, first column D
# + second column space = staged deletion + working tree clean for the file).
# These are the candidates for phantom deletion.
mapfile -t PHANTOM_PATHS < <(git status --porcelain | awk '/^D  / {print substr($0, 4)}')

if [ ${#PHANTOM_PATHS[@]} -eq 0 ]; then
    echo "[refresh-worktree] No phantom staged deletions detected. Working tree consistent with HEAD."
    exit 0
fi

echo "[refresh-worktree] Detected ${#PHANTOM_PATHS[@]} phantom staged deletion(s):"
for p in "${PHANTOM_PATHS[@]}"; do
    echo "  $p"
done
echo ""

# Cross-check: are these paths actually present in HEAD? If yes, they're
# genuine phantom-deletions (sibling-worktree push advanced HEAD past the
# index). If no, they're real staged deletions (the user staged a delete).
GENUINE_PHANTOM=()
REAL_DELETES=()
for p in "${PHANTOM_PATHS[@]}"; do
    if git show "HEAD:$p" >/dev/null 2>&1; then
        GENUINE_PHANTOM+=("$p")
    else
        REAL_DELETES+=("$p")
    fi
done

if [ ${#REAL_DELETES[@]} -gt 0 ]; then
    echo "[refresh-worktree] WARNING: ${#REAL_DELETES[@]} of these are real staged deletions (file does NOT exist in HEAD):"
    for p in "${REAL_DELETES[@]}"; do
        echo "  $p"
    done
    echo "  These are NOT phantom deletions; they're intentional. NOT touching them."
    echo ""
fi

if [ ${#GENUINE_PHANTOM[@]} -eq 0 ]; then
    echo "[refresh-worktree] No genuine phantom-deletions to fix."
    exit 0
fi

echo "[refresh-worktree] ${#GENUINE_PHANTOM[@]} genuine phantom-deletion(s) to restore from HEAD:"
for p in "${GENUINE_PHANTOM[@]}"; do
    echo "  $p"
done
echo ""

if $DRY_RUN; then
    echo "[refresh-worktree] DRY-RUN — would run: git checkout HEAD -- <paths above>"
    exit 0
fi

# Restore the working tree + index for the affected paths
git checkout HEAD -- "${GENUINE_PHANTOM[@]}"

echo "[refresh-worktree] Restored. Verifying clean state..."
REMAINING=$(git status --porcelain | awk '/^D  / {print substr($0, 4)}' | wc -l)
if [ "$REMAINING" -eq 0 ]; then
    echo "[refresh-worktree] PASS — no phantom staged deletions remain."
    exit 0
else
    echo "[refresh-worktree] WARN — $REMAINING phantom deletion(s) still present (these may be the real-deletes flagged above)."
    exit 1
fi
