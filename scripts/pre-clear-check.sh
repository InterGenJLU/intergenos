#!/usr/bin/env bash
# scripts/pre-clear-check.sh — fleet session-end discipline gate.
#
# Run before any owner-prepped clear OR session-end. Enforces four named
# exit paths for any uncommitted state:
#   (a) committed
#   (b) named stash (no anonymous WIP)
#   (c) state-record entry documenting pending work
#   (d) coordination broadcast announcing session-clear state
#
# This script checks (a) and (b) directly; (c) and (d) are reminded but not
# mechanically enforced (they require agent judgment about what's worth
# preserving and how to phrase it).
#
# Symmetric counterpart: scripts/pre-orient.sh (run at session-start).

set -uo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
if [ -z "$REPO_ROOT" ]; then
    echo "[pre-clear] ERROR: not in a git repo." >&2
    exit 2
fi
cd "$REPO_ROOT"

problems=0

echo "=== [pre-clear] working tree clean? ==="
STATUS=$(git status --porcelain)
TRACKED_DIRT=$(git status --porcelain | grep -v '^??' | wc -l)
UNTRACKED=$(git status --porcelain | grep '^??' | wc -l)
if [ "$TRACKED_DIRT" -gt 0 ]; then
    echo "  FAIL: $TRACKED_DIRT tracked file(s) have uncommitted changes."
    echo "$STATUS" | grep -v '^??' | head -10 | sed 's/^/    /'
    echo ""
    echo "  Resolve via ONE of the four named exit paths:"
    echo "    (a) commit the work (preferred for anything reasonably complete)"
    echo "    (b) named stash:   git stash push -m '<agent>-<topic>-$(date +%Y%m%d)'"
    echo "    (c) memory entry:  ~/.claude/projects/.../session_state_<agent>_<date>.md"
    echo "    (d) bus broadcast: SESSION-CLEAR with state preservation method"
    echo "  ANONYMOUS stashes (WIP on <branch>) are forbidden — provenance unknown."
    problems=$((problems + 1))
else
    echo "  PASS: no tracked-file changes."
fi

if [ "$UNTRACKED" -gt 0 ]; then
    echo ""
    echo "  INFO: $UNTRACKED untracked file(s) present:"
    git status --porcelain | grep '^??' | head -10 | sed 's/^/    /'
    echo "  These are non-blocking but consider: should they be committed, .gitignored, or moved?"
fi

echo ""
echo "=== [pre-clear] stash list (anonymous WIP forbidden) ==="
STASHES=$(git stash list)
if [ -n "$STASHES" ]; then
    echo "$STASHES" | sed 's/^/  /'
    ANON_COUNT=$(echo "$STASHES" | grep -cE '^stash@\{[0-9]+\}: WIP on' || true)
    if [ "$ANON_COUNT" -gt 0 ]; then
        echo ""
        echo "  FAIL: $ANON_COUNT anonymous WIP stash(es)."
        echo "  Resolve: drop or re-name. To inspect: git stash show -p stash@{N}"
        echo "    git stash drop stash@{N}             # if stale/junk"
        echo "    git stash pop stash@{N} && git stash push -m '<agent>-<topic>-<date>'  # if worth keeping"
        problems=$((problems + 1))
    fi
else
    echo "  PASS: empty."
fi

echo ""
echo "=== [pre-clear] master sync vs origin ==="
LOCAL=$(git rev-parse master 2>/dev/null || echo "")
REMOTE=$(git rev-parse origin/master 2>/dev/null || echo "")
if [ -n "$LOCAL" ] && [ -n "$REMOTE" ] && [ "$LOCAL" != "$REMOTE" ]; then
    BASE=$(git merge-base master origin/master 2>/dev/null || echo "")
    if [ "$BASE" = "$REMOTE" ]; then
        AHEAD=$(git rev-list --count origin/master..master)
        echo "  WARN: local master is AHEAD of origin by $AHEAD commit(s) — push before clear."
        problems=$((problems + 1))
    elif [ "$BASE" = "$LOCAL" ]; then
        echo "  INFO: local master is BEHIND origin — fetch happened during session."
    else
        echo "  WARN: local and origin master DIVERGED — rebase before clear."
        problems=$((problems + 1))
    fi
else
    echo "  PASS: in-sync."
fi

echo ""
echo "=== [pre-clear] reminders (not mechanically enforced) ==="
echo "  - Update handoff.md with current state + next-session pickup."
echo "  - Memory entry for any work-state worth carrying forward."
echo "  - Bus broadcast: SESSION-CLEAR <agent>, master at <sha>, preserved-via <method>."

echo ""
if [ "$problems" -eq 0 ]; then
    echo "[pre-clear] PASS — safe to clear."
    exit 0
else
    echo "[pre-clear] FAIL — $problems issue(s); resolve before clearing to avoid orphan state."
    exit 1
fi
