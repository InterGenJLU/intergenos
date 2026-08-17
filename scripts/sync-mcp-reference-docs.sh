#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# sync-mcp-reference-docs.sh — mirror docs/operations/ into the InterGen
# comms-bus MCP reference store so the intergen://reference/ resources stay
# current with the repo.
#
# WHY THIS EXISTS: the reference docs were populated by a one-time manual copy
# (2026-05-18/-23) and never re-synced, so the whole set drifted stale for weeks
# (operations-02 still said "19-phase" + "password permanently retired", and
# 09/10 kept their pre-rename filenames after the repo renamed them to
# gbc-iteration-methodology / iteration-resume-builds). This script + the
# intergen-mcp-doc-sync.timer that runs it close that drift.
#
# MAPPING:
#   docs/operations/<NN-name>.md  ->  <REF_DIR>/operations-<NN-name>.md
#   docs/operations/README.md     ->  <REF_DIR>/operations-README.md
# Non-numbered operations docs (eula-helper-flow, first-publish-runbook,
# pure-python-github-source-pattern) are intentionally NOT exposed — the
# reference set is the curated numbered-topics + README, matching what was
# served before. Stale <REF_DIR>/operations-*.md whose repo source no longer
# exists are REMOVED (handles renames/deletions). Non-operations reference docs
# (e.g. flux_usage.md) are left untouched.
#
# The MCP server registers reference resources at STARTUP, so this restarts
# intergen-mcp.service (with --restart) ONLY when a reference file actually
# changed. Idempotent; safe to run on a timer.
#
# USAGE (on the VPS, from a repo clone that has been `git pull`ed):
#   sudo bash scripts/sync-mcp-reference-docs.sh --restart
#   bash scripts/sync-mcp-reference-docs.sh            # sync files, do not restart
#
# ENV overrides: DOCS_DIR, REF_DIR, SERVICE.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"/.. && pwd)"
DOCS_DIR="${DOCS_DIR:-$REPO_ROOT/docs/operations}"
REF_DIR="${REF_DIR:-/srv/intergen-mcp/reference}"
SERVICE="${SERVICE:-intergen-mcp.service}"

DO_RESTART=0
[ "${1:-}" = "--restart" ] && DO_RESTART=1

[ -d "$DOCS_DIR" ] || { echo "FATAL: docs dir missing: $DOCS_DIR" >&2; exit 1; }
[ -d "$REF_DIR" ]  || { echo "FATAL: reference dir missing: $REF_DIR" >&2; exit 1; }

changed=0
declare -A want   # reference filenames that have a current repo source

# 1. Mirror numbered operations docs + README into operations-<name>.md
for src in "$DOCS_DIR"/[0-9][0-9]-*.md "$DOCS_DIR"/README.md; do
    [ -f "$src" ] || continue
    base="$(basename "$src")"
    want["operations-$base"]=1
    dst="$REF_DIR/operations-$base"
    if ! cmp -s "$src" "$dst"; then
        install -m644 "$src" "$dst"
        echo "synced: operations-$base"
        changed=1
    fi
done

# 2. Remove stale reference/operations-*.md whose repo source is gone (renames)
for f in "$REF_DIR"/operations-*.md; do
    [ -e "$f" ] || continue
    b="$(basename "$f")"
    if [ -z "${want[$b]:-}" ]; then
        rm -f "$f"
        echo "removed stale: $b"
        changed=1
    fi
done

if [ "$changed" -eq 0 ]; then
    echo "reference docs already current — no change."
    exit 0
fi

echo "reference docs changed."
if [ "$DO_RESTART" -eq 1 ]; then
    systemctl restart "$SERVICE"
    echo "restarted $SERVICE"
else
    echo "(re-run with --restart, or restart $SERVICE, for the server to serve the updates)"
fi
