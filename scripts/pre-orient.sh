#!/usr/bin/env bash
# scripts/pre-orient.sh — fleet session-start orient gate.
#
# Run at the start of every fresh session to surface git state. Any non-empty
# output from the status/stash/divergence checks should HALT new work until
# investigated — uncommitted state at session-start can be inherited orphan
# state from a prior session that didn't clean up cleanly.
#
# Symmetric counterpart: scripts/pre-clear-check.sh (run at session-end).

set -uo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
if [ -z "$REPO_ROOT" ]; then
    echo "[pre-orient] ERROR: not in a git repo. cd into the repo first." >&2
    exit 2
fi
cd "$REPO_ROOT"

dirty=0

echo "=== [pre-orient] git fetch origin ==="
if ! git fetch origin 2>&1 | tail -3; then
    echo "[pre-orient] WARN: git fetch failed (network? offline session?)"
fi

echo ""
echo "=== [pre-orient] master tip vs origin/master ==="
LOCAL=$(git rev-parse master 2>/dev/null || echo "(no master)")
REMOTE=$(git rev-parse origin/master 2>/dev/null || echo "(no origin/master)")
echo "  local master:  $LOCAL"
echo "  origin/master: $REMOTE"
if [ "$LOCAL" != "$REMOTE" ] && [ "$LOCAL" != "(no master)" ] && [ "$REMOTE" != "(no origin/master)" ]; then
    BASE=$(git merge-base master origin/master 2>/dev/null || echo "")
    if [ "$BASE" = "$LOCAL" ]; then
        AHEAD=$(git rev-list --count master..origin/master)
        echo "  STATUS: local is BEHIND origin by $AHEAD commit(s)."
        echo "  Recent inbound (newest first):"
        git log --oneline master..origin/master | head -5 | sed 's/^/    /'
        dirty=1
    elif [ "$BASE" = "$REMOTE" ]; then
        AHEAD=$(git rev-list --count origin/master..master)
        echo "  STATUS: local is AHEAD of origin by $AHEAD commit(s) (push pending)."
    else
        echo "  STATUS: local and origin have DIVERGED — needs rebase."
        dirty=1
    fi
else
    echo "  STATUS: in-sync."
fi

echo ""
echo "=== [pre-orient] working tree status ==="
STATUS=$(git status --porcelain)
if [ -n "$STATUS" ]; then
    echo "$STATUS" | head -20
    TRACKED_DIRT=$(git status --porcelain | grep -v '^??' | wc -l)
    if [ "$TRACKED_DIRT" -gt 0 ]; then
        echo ""
        echo "  WARN: $TRACKED_DIRT tracked file(s) have uncommitted changes."
        echo "  HALT-and-investigate: this may be inherited orphan state from a prior session."
        echo "    git diff --staged   # see staged changes"
        echo "    git diff            # see unstaged changes"
        echo "    git log -1          # confirm what HEAD currently is"
        echo "  If state is from another agent: bus them with provenance question."
        echo "  If no agent claims it: capture rescue patch + git checkout HEAD -- + memory note."
        dirty=1
    fi
else
    echo "  STATUS: clean."
fi

echo ""
echo "=== [pre-orient] stash list ==="
STASHES=$(git stash list)
if [ -n "$STASHES" ]; then
    echo "$STASHES"
    if echo "$STASHES" | grep -qE '^stash@\{[0-9]+\}: WIP on'; then
        echo ""
        echo "  WARN: anonymous WIP stash(es) detected — provenance unknown."
        echo "  Per discipline: stashes should carry '<agent>-<topic>-<date>' names."
        echo "  Inspect via: git stash show -p stash@{N}"
        dirty=1
    fi
else
    echo "  STATUS: empty."
fi

echo ""
echo "=== [pre-orient] worktree list ==="
git worktree list

echo ""
echo "=== [pre-orient] active branch + recent commits ==="
echo "  branch: $(git branch --show-current)"
echo "  recent commits (newest first):"
git log --oneline -5 | sed 's/^/    /'

echo ""
if [ "$dirty" -eq 0 ]; then
    echo "[pre-orient] CLEAN — proceed with orient + work."
    exit 0
else
    echo "[pre-orient] DIRTY — HALT new work and investigate the items above."
    exit 1
fi
