#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# scripts/check-line-citations.sh — validate <file>:<line> citations in
# markdown docs against actual repo state.
#
# S-D 5 (USA-1 audit): audit docs / tracker / matrix files cite source
# code via `[<file>:<line>](...path#L<line>)` markdown links. These
# anchors rot mechanically when the cited source file edits — USA-1
# W-W1 found 8 of 9 kernel-config citations off by 6-12 lines.
#
# This script catches that drift by parsing each cited (file, line)
# pair, resolving the file relative to repo root (or via tree search
# for bare filenames), and verifying the line number is in bounds.
# Since the 2026-08-16 extension it ALSO verifies a SEMANTIC anchor:
# when the citing line carries a backticked symbol (an identifier,
# optionally `=value`), the cited line range must contain that
# identifier. A citation whose line carries no symbol keeps the
# bounds-only check. (The v1 header said semantic verification was
# out of scope; that scope statement was retired with the extension.)
#
# Citation patterns recognized:
#   [<file>:<line>](<relpath>#L<line>)             — markdown link
#   [<file>:<start>-<end>](<relpath>#L<start>-L<end>)
#   `<file>:<line>`  /  `<file>:<start>-<end>`     — backticked plain text
#
# Status categories per citation:
#   OK         — file exists, line in bounds (and anchor found, if any)
#   OUT-OF-BOUNDS — file exists but line > total lines (drift)
#   SEMANTIC-MISMATCH — in bounds, but the cited range does not contain
#                the backticked symbol(s) on the citing line
#   MISSING    — file does not exist at the cited path or anywhere in tree
#   AMBIGUOUS  — bare filename resolves to multiple tree locations (citation
#                must be qualified with a path prefix to disambiguate)
#   INVERTED RANGE / UNREADABLE — malformed range / unreadable target
#
# Files scanned: arguments OR the default sweep — ALL of docs/ plus the
# repo-root markdown files plus every package's README.md. (The v1 sweep
# named docs/audit/, a directory that does not exist in this tree, so the
# default run validated nothing while printing green — measured
# 2026-08-16; widened the same day.) Two exclusions, each with a reason:
#   docs/lfs-13.0/  — vendored upstream book, not our citations.
#   docs/research/  — preserved dated historical records (decided
#                     2026-08-16, label-and-keep): their citations
#                     describe the trees of their day and are not
#                     maintained against the current tree.
#
# Usage:
#   scripts/check-line-citations.sh                  # default sweep
#   scripts/check-line-citations.sh docs/audit/*.md  # explicit set
#   scripts/check-line-citations.sh --staged         # only staged .md files
#                                                      (pre-commit hook mode)
#
# Exit codes:
#   0 — all citations resolve cleanly
#   1 — one or more drifted/broken citations found
#   2 — script invocation error

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"/.. && pwd)"
cd "$REPO_ROOT" || exit 2

# Hand off to the Python implementation. The python script does all the
# heavy lifting (single pass, structured output); this shell wrapper
# exists only to handle argument modes (--staged + default sweep) and
# keep the invocation surface familiar to the orchestrator + hooks.

# --diff-only mode: hand straight off to the python script, which reads
# the staged diff itself. Used by the pre-commit hook: only validates
# citations in lines NEW to this commit, leaving pre-existing drift
# alone (so commits aren't blocked by baseline violations).
if [ $# -ge 1 ] && [ "$1" = "--diff-only" ]; then
    exec python3 "$REPO_ROOT/scripts/check_line_citations.py" --diff-only
fi

declare -a FILES
if [ $# -ge 1 ] && [ "$1" = "--staged" ]; then
    mapfile -t FILES < <(git diff --cached --name-only --diff-filter=ACMR -- '*.md' 2>/dev/null)
    if [ "${#FILES[@]}" -eq 0 ]; then
        echo "[check-line-citations] no staged .md files; nothing to check"
        exit 0
    fi
elif [ $# -ge 1 ]; then
    FILES=("$@")
else
    mapfile -t FILES < <(
        find docs -type f -name '*.md' \
            -not -path 'docs/lfs-13.0/*' \
            -not -path 'docs/research/*' 2>/dev/null
        find . -maxdepth 1 -type f -name '*.md' -printf '%P\n' 2>/dev/null
        find packages -mindepth 3 -maxdepth 3 -name 'README.md' 2>/dev/null
    )
fi

exec python3 "$REPO_ROOT/scripts/check_line_citations.py" "${FILES[@]}"
