#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# check-forbidden-imports.sh — Gate 2 enforcement from
# docs/operations/pure-python-github-source-pattern.md §4.
#
# For each transitive dep dispositioned as §2.6(b) verified-unused, the recipe
# installs a forbidden-import entry that flunks any new Python import of the
# surface that would re-introduce the dep at runtime. A single grep pattern
# misses several import grammars; this script enumerates ALL of:
#
#   from MODULE import ...
#   import MODULE
#   import MODULE as X
#   __import__("MODULE")
#   importlib.import_module("MODULE")
#
# per forbidden-MODULE entry. ast-walk-based detection is the principled
# upgrade once the verified-unused set grows past ~2 entries (recommendation
# in the pattern doc §4 Gate 2 body).
#
# FORBIDDEN_ENTRIES is the active list — each entry is a tab-separated row:
#   MODULE<TAB>RATIONALE
# An empty FORBIDDEN_ENTRIES is the baseline state at doc landing; per-recipe
# entries get added as §2.6(b) dispositions activate.
#
# Run from repo root.
#
# Exit codes:
#   0 — gate PASS (no forbidden imports found, or empty input)
#   1 — gate FAIL (at least one forbidden import found in the searched tree)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ----------------------------------------------------------------------------
# Forbidden-import entries.
#
# Format: MODULE<TAB>RATIONALE   (one per row; rows separated by newlines)
# Example (currently inactive — pygments ships per the recorded decision NIT-A option (a)):
#   rich.syntax	rich.syntax pulls pygments at runtime; verified-unused entry
# ----------------------------------------------------------------------------
FORBIDDEN_ENTRIES=$(cat <<'EOF'
EOF
)

# Where we search. Each tier-name reflects a Python-importing surface in our
# tree. Adjust this list as new Python-bearing trees land.
SEARCH_PATHS=(intergen installer pkm igos-build scripts)

if [ -z "${FORBIDDEN_ENTRIES//[[:space:]]/}" ]; then
    echo "[check-forbidden-imports] no active forbidden-import entries; gate PASS (empty input)"
    exit 0
fi

VIOLATIONS=0

# Use a temporary file to allow IFS-respecting line iteration of the heredoc.
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
printf '%s\n' "$FORBIDDEN_ENTRIES" > "$tmp"

while IFS=$'\t' read -r module rationale; do
    # Skip blank lines + comments
    case "$module" in
        '' | \#*) continue ;;
    esac

    # Build the 5 grep patterns covering Python's import grammars.
    # We grep across $SEARCH_PATHS for any of them.
    found=0
    for path in "${SEARCH_PATHS[@]}"; do
        [ -d "$path" ] || continue

        # Pattern 1: from MODULE import ...
        # Pattern 2: import MODULE (word-boundary terminated)
        # Pattern 3: import MODULE as ...
        # Pattern 4: __import__("MODULE") or __import__('MODULE')
        # Pattern 5: importlib.import_module("MODULE") / ('MODULE')
        if grep -rnE \
            -e "^[[:space:]]*from[[:space:]]+${module//./\\.}[[:space:]]+import[[:space:]]" \
            -e "^[[:space:]]*import[[:space:]]+${module//./\\.}([[:space:]]|$)" \
            -e "^[[:space:]]*import[[:space:]]+${module//./\\.}[[:space:]]+as[[:space:]]" \
            -e "__import__\\(['\"]${module//./\\.}['\"]" \
            -e "importlib\\.import_module\\(['\"]${module//./\\.}['\"]" \
            --include='*.py' "$path" 2>/dev/null; then
            found=1
        fi
    done

    if [ "$found" -eq 1 ]; then
        echo "[check-forbidden-imports] FAIL: forbidden import of '$module' — $rationale" >&2
        VIOLATIONS=$((VIOLATIONS + 1))
    fi
done < "$tmp"

if [ "$VIOLATIONS" -gt 0 ]; then
    echo ""
    echo "[check-forbidden-imports] $VIOLATIONS violation(s) — see docs/operations/pure-python-github-source-pattern.md §2.6 + §4 Gate 2" >&2
    exit 1
fi

echo "[check-forbidden-imports] no forbidden imports found across ${#SEARCH_PATHS[@]} search paths; gate PASS"
exit 0
