#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# scripts/anchor-tracker.sh — advance the TRACKER anchor to the public repo's
# current HEAD + commit + push the private repo. Invoked by the public repo's
# pre-push gate when an out-of-date anchor is detected.
#
# Workflow:
#   1. Read public-repo's about-to-be-pushed HEAD (passed as $1, or auto-detect)
#   2. Read TRACKER.md's current ANCHOR line
#   3. If they match, no-op (push proceeds)
#   4. Otherwise, rewrite the ANCHOR line in TRACKER.md to point at HEAD
#   5. git add + commit (conventional-commit-formatted) + push private repo
#   6. Public repo's pre-push gate then re-verifies + allows the public push
#
# This is the anti-drift enforcement: every public-repo push to master
# advances the TRACKER anchor in lock-step. Drift becomes mechanically
# impossible at the push boundary.
#
# Operator workflow note: substantive TRACKER content updates (STATE banner
# refresh, new entries, status changes) are handled manually and happen
# as separate manual edits + commits to the private repo. This script only
# advances the ANCHOR line — the bookkeeping piece, not the content piece.
# Operator should still update STATE banner + relevant entries when state
# materially changes; this script ensures the anchor-bookkeeping always
# tracks the public-repo HEAD without operator action.
#
# WHO THE ANCHOR COMMIT IS ATTRIBUTED TO (required)
# -------------------------------------------------
# The anchor commit carries a Co-Authored-By trailer, and a trailer is a
# disclosure of who did the work. Until 2026-08-25 this script carried a
# hard-coded trailer naming one specific model, which it asserted regardless of
# what was actually running — a disclosure the script could not know to be true.
#
# The trailer value is now read from INTERGENOS_COMMIT_COAUTHOR, which the
# session or seat driving the push sets to its own "Name <address>", e.g.
#
#   export INTERGENOS_COMMIT_COAUTHOR='Example Author <noreply@example.invalid>'
#
# When it is unset or malformed this script REFUSES (exit 2) rather than
# stamping a guess. An unattributed commit is the failure this prevents; a
# refused push is recoverable in one command. Also documented in the private
# repository's docs/operations/branch-model.md, anchor section.
#
# Usage:
#   scripts/anchor-tracker.sh                    # auto-detect HEAD
#   scripts/anchor-tracker.sh <SHA>              # anchor to specific SHA
#   scripts/anchor-tracker.sh --dry-run [<SHA>]  # rehearse; change nothing
#
# --dry-run prints the anchor line it would write and the private-repo commit it
# would make, and touches neither repository. It exists so the pre-push gate that
# calls this script can be proved against the REAL hook without performing a real
# outward write. It applies exactly the same checks as the real path — a
# rehearsal that passes where the real run would refuse is a rehearsal of a
# different script.
#
# Exit codes:
#   0 — anchor advanced (or already current; no-op; or dry run completed)
#   1 — anchor advancement failed (network, conflict, etc.)
#   2 — script invocation error (bad arguments, or no co-author stated)

set -euo pipefail

# ---- Argument parsing ----------------------------------------------------
# ANCHOR_TRACKER_DRY_RUN=1 is the environment equivalent of --dry-run. It exists
# because the caller that most needs rehearsing is the pre-push gate, which
# invokes this script with a fixed argument list and cannot be asked to add a
# flag. An environment variable reaches the child process unchanged, so the gate
# can be proved end-to-end against the real hook without an outward write.
DRY_RUN=0
[ "${ANCHOR_TRACKER_DRY_RUN:-0}" = "1" ] && DRY_RUN=1
POSITIONAL=()
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --)        shift; while [ $# -gt 0 ]; do POSITIONAL+=("$1"); shift; done ;;
        -*)        echo "ERROR: unknown option: $1" >&2
                   echo "  usage: $(basename "$0") [--dry-run] [<SHA>]" >&2
                   exit 2 ;;
        *)         POSITIONAL+=("$1"); shift ;;
    esac
done
if [ "${#POSITIONAL[@]}" -gt 1 ]; then
    echo "ERROR: at most one SHA may be given; got ${#POSITIONAL[@]}" >&2
    exit 2
fi
set -- ${POSITIONAL[@]+"${POSITIONAL[@]}"}

# ---- Who this commit is attributed to (checked BEFORE any work) ----------
# Checked up front, and on the rehearsal path too, so --dry-run refuses for the
# same reason and at the same point the real run would.
COAUTHOR="${INTERGENOS_COMMIT_COAUTHOR:-}"
if [ -z "$COAUTHOR" ]; then
    echo "ERROR: INTERGENOS_COMMIT_COAUTHOR is not set." >&2
    echo "  The anchor commit carries a Co-Authored-By trailer, which discloses" >&2
    echo "  who did the work. This script will not guess it." >&2
    echo "  Set it to the author driving this push, then retry:" >&2
    echo "    export INTERGENOS_COMMIT_COAUTHOR='Name <address@example>'" >&2
    exit 2
fi
if ! printf '%s' "$COAUTHOR" | grep -qE '^[^<>]+ <[^<>[:space:]]+@[^<>[:space:]]+>$'; then
    echo "ERROR: INTERGENOS_COMMIT_COAUTHOR is malformed: '$COAUTHOR'" >&2
    echo "  A Co-Authored-By trailer value must be 'Name <address@example>'." >&2
    echo "  Refusing rather than stamping an unusable trailer into a commit." >&2
    exit 2
fi

# Public-repo path defaults to this script's parent dir (anchor-tracker.sh
# lives in scripts/, so parent is the repo root). Override via env for
# non-standard layouts.
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
PUBLIC_REPO="${INTERGENOS_PUBLIC_REPO:-$(cd "$SCRIPT_DIR/.." && pwd)}"

# Private-repo discovery chain (first match wins):
#   1. $INTERGENOS_PRIVATE_REPO env var (explicit override)
#   2. $HOME/intergenos-private (common layout — home-dir sibling)
#   3. $PUBLIC_REPO/../intergenos-private (sibling-of-public-repo layout)
# Fails with actionable error if none resolve to a git repo.
if [ -n "${INTERGENOS_PRIVATE_REPO:-}" ]; then
    PRIVATE_REPO="$INTERGENOS_PRIVATE_REPO"
elif [ -d "${HOME:-/nonexistent}/intergenos-private/.git" ]; then
    PRIVATE_REPO="$HOME/intergenos-private"
elif [ -d "$PUBLIC_REPO/../intergenos-private/.git" ]; then
    PRIVATE_REPO="$(cd "$PUBLIC_REPO/.." && pwd)/intergenos-private"
else
    echo "ERROR: private repo not found via any of:" >&2
    echo "  - \$INTERGENOS_PRIVATE_REPO env var" >&2
    echo "  - \$HOME/intergenos-private" >&2
    echo "  - \$PUBLIC_REPO/../intergenos-private" >&2
    echo "  Set INTERGENOS_PRIVATE_REPO to the clone path of InterGenJLU/intergenos-private" >&2
    exit 2
fi
TRACKER="$PRIVATE_REPO/TRACKER.md"

[ -d "$PUBLIC_REPO/.git" ] || { echo "ERROR: PUBLIC_REPO ($PUBLIC_REPO) not a git repo" >&2; exit 2; }
[ -d "$PRIVATE_REPO/.git" ] || { echo "ERROR: PRIVATE_REPO ($PRIVATE_REPO) not a git repo" >&2; exit 2; }
[ -f "$TRACKER" ] || { echo "ERROR: TRACKER not found at $TRACKER" >&2; exit 2; }

# Determine target SHA.
if [ $# -ge 1 ]; then
    TARGET_SHA="$1"
else
    TARGET_SHA=$(git -C "$PUBLIC_REPO" rev-parse HEAD)
fi
TARGET_SHORT=$(git -C "$PUBLIC_REPO" rev-parse --short=8 "$TARGET_SHA")

# Read current anchor.
CURRENT_ANCHOR=$(grep -oE '<!-- ANCHOR: public-master HEAD [a-f0-9]+ -->' "$TRACKER" | head -1 | awk '{print $5}' || true)

if [ -z "$CURRENT_ANCHOR" ]; then
    echo "[anchor-tracker] no ANCHOR line in TRACKER; cannot proceed without bootstrap" >&2
    exit 1
fi

if [ "$CURRENT_ANCHOR" = "$TARGET_SHORT" ] || [ "$CURRENT_ANCHOR" = "$TARGET_SHA" ]; then
    echo "[anchor-tracker] anchor already at $CURRENT_ANCHOR; no-op"
    exit 0
fi

if [ "$DRY_RUN" = "1" ]; then
    echo "[anchor-tracker] DRY RUN — nothing is written to either repository."
    echo "  public repo:      $PUBLIC_REPO"
    echo "  private repo:     $PRIVATE_REPO"
    echo "  tracker file:     $TRACKER"
    echo "  anchor line now:  <!-- ANCHOR: public-master HEAD $CURRENT_ANCHOR -->"
    echo "  anchor line would become:"
    echo "                    <!-- ANCHOR: public-master HEAD $TARGET_SHORT -->"
    echo "  would commit to:  $PRIVATE_REPO (subject below), then push it"
    echo "    chore(anchor): advance TRACKER anchor to public-master \`${TARGET_SHORT}\`"
    echo "  attributed to:    Co-Authored-By: $COAUTHOR"
    exit 0
fi

echo "[anchor-tracker] advancing anchor: $CURRENT_ANCHOR → $TARGET_SHORT"

# Rewrite the ANCHOR line in place.
python3 - "$TRACKER" "$TARGET_SHORT" <<'PY'
import sys, re, pathlib
path = pathlib.Path(sys.argv[1])
new_sha = sys.argv[2]
text = path.read_text()
new_text, n = re.subn(
    r'<!-- ANCHOR: public-master HEAD [a-f0-9]+ -->',
    f'<!-- ANCHOR: public-master HEAD {new_sha} -->',
    text,
    count=1,
)
if n != 1:
    sys.stderr.write(f"ERROR: expected exactly 1 ANCHOR line, found {n}\n")
    sys.exit(1)
path.write_text(new_text)
PY

# Stage + commit + push private repo.
cd "$PRIVATE_REPO"
git add TRACKER.md
git commit -m "$(cat <<EOF
chore(anchor): advance TRACKER anchor to public-master \`${TARGET_SHORT}\`

Auto-generated by scripts/anchor-tracker.sh as part of the public-repo
pre-push anti-drift discipline. Anchor advances from \`${CURRENT_ANCHOR}\` →
\`${TARGET_SHORT}\` to track the public-repo HEAD being pushed.

This commit is bookkeeping only — substantive TRACKER content updates
(STATE banner refresh, entry changes) land separately as manually-authored
commits.

Co-Authored-By: ${COAUTHOR}
EOF
)" >/dev/null

git push >/dev/null 2>&1 || {
    echo "[anchor-tracker] ERROR: private-repo push failed" >&2
    echo "[anchor-tracker] anchor committed locally at $(git -C "$PRIVATE_REPO" rev-parse --short HEAD) but not pushed" >&2
    exit 1
}

echo "[anchor-tracker] anchor advanced + pushed: $TARGET_SHORT"
exit 0
