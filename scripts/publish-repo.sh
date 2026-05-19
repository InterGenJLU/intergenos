#!/bin/bash
# publish-repo.sh — Publish binary repository to repo.intergenos.org
#
# E1.B.7 publish orchestrator. Wraps pkm.repo.generate_index() +
# sign_index() library functions and rsyncs to the remote repo.
#
# Usage:
#   scripts/publish-repo.sh                          # default archive dir + remote
#   scripts/publish-repo.sh --dry-run                # check what WOULD be published
#   scripts/publish-repo.sh --archive-dir /path/to/  # custom archive dir
#   scripts/publish-repo.sh --gpg-key S2             # sign with backup key
#
# Environment overrides:
#   PUBLISH_REMOTE_USER       (default: intergenos)
#   PUBLISH_REMOTE_HOST       (default: origin.intergenstudios.com)
#   PUBLISH_REMOTE_PATH       (default: /home/intergenos/repo/x86_64)
#   PUBLISH_SOURCES_DIR       (default: build/sources-archives — where
#                              build-source-archives.py emits .igos.src.tar.gz
#                              corresponding-source archives for each package)
#
# Prerequisites:
#   - Packages built and archived at /var/lib/igos/archives/
#   - SSH key auth to origin.intergenstudios.com configured
#   - NK#1 (or NK#2) release key available to GPG
#   - Source archives generated via scripts/build-source-archives.py
#     (the source-availability commitment in SOURCES.md §6d depends on
#     these landing in <host>/x86_64/current/sources/ alongside the binaries)
set -e -o pipefail

ARCHIVE_DIR="/var/lib/igos/archives"
SOURCES_DIR="${PUBLISH_SOURCES_DIR:-build/sources-archives}"
REMOTE_USER="${PUBLISH_REMOTE_USER:-intergenos}"
REMOTE_HOST="${PUBLISH_REMOTE_HOST:-origin.intergenstudios.com}"
REMOTE_PATH="${PUBLISH_REMOTE_PATH:-/home/intergenos/repo/x86_64}"
GPG_KEY="NK1"
DRY_RUN=false
SKIP_SOURCES=false
SKIP_TRANSPARENCY=false

# Transparency log substrate (L-024). Each publish appends a structured
# commit to this git repo containing the signed-index sha256, size, and
# signer fingerprint. Git provides the append-only property assuming the
# remote master branch is configured for force-push protection. The
# audit row L-024 proposed git-repo-as-append-only-log as the minimum
# viable implementation; Sigstore Rekor v2 integration is queued as
# v1.1 enhancement (second attestation target, not a replacement).
TRANSPARENCY_GIT_REMOTE="${PUBLISH_TRANSPARENCY_REMOTE:-git@github.com:InterGenJLU/intergenos-mirror-backup.git}"
TRANSPARENCY_LOCAL="${PUBLISH_TRANSPARENCY_LOCAL:-$HOME/.intergenos-transparency-log}"

# Release key fingerprints
declare -A GPG_KEY_FPS
GPG_KEY_FPS[NK1]="D7AA641D81ACD690C5AD865E7276E14DD8886BFE"
GPG_KEY_FPS[NK2]="81DD223F9BA9B3F2AFBFFC5AFA24B042975F775E"
GPG_KEY_FPS[S1]="D7AA641D81ACD690C5AD865E7276E14DD8886BFE"
GPG_KEY_FPS[S2]="81DD223F9BA9B3F2AFBFFC5AFA24B042975F775E"

usage() {
    cat <<EOF
Usage: $0 [--dry-run] [--archive-dir DIR] [--gpg-key NK1|NK2] [--skip-sources]

  --dry-run        Show what would be uploaded; don't actually publish.
  --archive-dir    Override binary archive directory (default: $ARCHIVE_DIR).
  --gpg-key        Sign with NK1 (primary) or NK2 (backup). Default: NK1.
  --skip-sources   Emergency override — publish binaries without their
                   corresponding-source archives. Use only when source
                   generation is a known follow-on (not normal flow);
                   defaults to fail-closed so binary publish always
                   accompanies its SOURCES.md §6d source-availability
                   commitment.
  --skip-transparency
                   Emergency override — publish without appending to the
                   transparency-log git repo. Use only when the log
                   substrate is genuinely unavailable; defaults to
                   fail-closed so every published index is recorded in
                   the append-only public log (L-024).
EOF
    exit 1
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)         DRY_RUN=true; shift ;;
        --archive-dir)     ARCHIVE_DIR="$2"; shift 2 ;;
        --gpg-key)         GPG_KEY="$2"; shift 2 ;;
        --skip-sources)    SKIP_SOURCES=true; shift ;;
        --skip-transparency) SKIP_TRANSPARENCY=true; shift ;;
        -h|--help)         usage ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

if [ ! -d "$ARCHIVE_DIR" ]; then
    echo "ERROR: Archive directory does not exist: $ARCHIVE_DIR" >&2
    exit 1
fi

GPG_FP="${GPG_KEY_FPS[$GPG_KEY]}"
if [ -z "$GPG_FP" ]; then
    echo "ERROR: Unknown GPG key: $GPG_KEY (valid: NK1, NK2)" >&2
    exit 1
fi

COUNT=$(ls "$ARCHIVE_DIR"/*.igos.tar.gz 2>/dev/null | wc -l)

echo "=== InterGenOS Repository Publish ==="
echo "Archive dir: $ARCHIVE_DIR"
echo "Packages:    $COUNT .igos.tar.gz files"
echo "GPG key:     $GPG_KEY ($GPG_FP)"
echo "Remote:      $REMOTE_USER@$REMOTE_HOST:$REMOTE_PATH"
echo ""

# Preflight checks — fail-fast before expensive operations
echo "[preflight] Checking SSH connectivity..."
ssh -o BatchMode=yes -o ConnectTimeout=10 \
    "${REMOTE_USER}@${REMOTE_HOST}" true \
    || { echo "ERROR: SSH auth to ${REMOTE_USER}@${REMOTE_HOST} failed" >&2; exit 1; }
echo "  OK — SSH reachable"

echo "[preflight] Checking GPG key availability..."
gpg --list-secret-keys "$GPG_FP" >/dev/null 2>&1 \
    || { echo "ERROR: GPG key $GPG_KEY ($GPG_FP) not available" >&2; exit 1; }
echo "  OK — GPG key available"

# Step 1: Generate InterGenOS.db index
echo "[1/4] Generating InterGenOS.db..."
python3 -c "
import sys
sys.path.insert(0, '.')
from pkm.repo import generate_index
path = generate_index('$ARCHIVE_DIR', arch='x86_64')
print(f'Index written: {path}')
" || { echo "ERROR: Index generation failed" >&2; exit 1; }

INDEX_PATH="$ARCHIVE_DIR/InterGenOS.db"
if [ ! -f "$INDEX_PATH" ]; then
    echo "ERROR: Index not found after generation: $INDEX_PATH" >&2
    exit 1
fi
echo "  OK — $(stat -c%s "$INDEX_PATH") bytes"

# Step 2: PGP-sign the index
echo "[2/4] Signing InterGenOS.db..."
python3 -c "
import sys
sys.path.insert(0, '.')
from pkm.repo import sign_index
path = sign_index('$INDEX_PATH', gpg_key_id='$GPG_FP')
print(f'Signature written: {path}')
" || { echo "ERROR: Index signing failed" >&2; exit 1; }

SIG_PATH="${INDEX_PATH}.sig"
echo "  OK — $(stat -c%s "$SIG_PATH") bytes"

if [ "$DRY_RUN" = true ]; then
    echo ""
    echo "=== DRY RUN — not publishing ==="
    echo "Would rsync:"
    echo "  $ARCHIVE_DIR/*.igos.tar.gz  →  $REMOTE_HOST:staging/"
    echo "  $INDEX_PATH                 →  $REMOTE_HOST:staging/InterGenOS.db"
    echo "  $SIG_PATH                   →  $REMOTE_HOST:staging/InterGenOS.db.sig"
    if [ "$SKIP_SOURCES" = true ]; then
        echo "  (source archives intentionally omitted via --skip-sources)"
    else
        SRC_GLOB_DRY=( "$SOURCES_DIR"/*.igos.src.tar.gz )
        if [ -e "${SRC_GLOB_DRY[0]}" ]; then
            echo "  $SOURCES_DIR/*.igos.src.tar.gz  →  $REMOTE_HOST:staging/sources/  (${#SRC_GLOB_DRY[@]} archives)"
        else
            echo "  (NO source archives in $SOURCES_DIR/ — real publish would fail-closed)"
        fi
    fi
    echo "  Then: promote staging/ → live/"
    exit 0
fi

# Step 3: Rsync to timestamped staging directory on remote
# Timestamped staging prevents M2 race condition (concurrent invocations).
STAGING_DIR="_staging-$(date -u +%Y%m%dT%H%M%SZ)"
STAGING_PATH="${REMOTE_PATH}/${STAGING_DIR}"
STAGING_RSYNC="ssh://${REMOTE_USER}@${REMOTE_HOST}${STAGING_PATH}"

echo "[3/4] Uploading to ${STAGING_DIR}..."
rsync -av --mkpath \
    "$ARCHIVE_DIR"/*.igos.tar.gz \
    "$INDEX_PATH" \
    "$SIG_PATH" \
    "$STAGING_RSYNC/" \
    || { echo "ERROR: rsync to staging failed" >&2; exit 1; }
echo "  OK — packages + index + signature uploaded"

# Source archives — deliver against the SOURCES.md §6d corresponding-source
# commitment. Land them at <staging>/sources/ so they're reachable at
# repo.intergenos.org/x86_64/current/sources/ post-promote. Fail-closed
# if absent — publishing binaries without their corresponding source
# violates the SOURCES.md binding commitment. The --skip-sources flag
# is the operator escape hatch for known follow-on cases.
if [ "$SKIP_SOURCES" = true ]; then
    echo "  SKIP — --skip-sources flag set; source archives intentionally omitted."
    echo "         Re-run scripts/build-source-archives.py + publish again before"
    echo "         considering this snapshot SOURCES.md-compliant."
else
    SRC_GLOB=( "$SOURCES_DIR"/*.igos.src.tar.gz )
    if [ ! -e "${SRC_GLOB[0]}" ]; then
        echo "ERROR: no .igos.src.tar.gz in $SOURCES_DIR/" >&2
        echo "       publishing binaries without their corresponding-source archives" >&2
        echo "       violates the SOURCES.md §6d commitment. Run:" >&2
        echo "         scripts/build-source-archives.py" >&2
        echo "       to generate them, then re-run this publish. Override:" >&2
        echo "         scripts/publish-repo.sh --skip-sources  (emergency only)" >&2
        exit 1
    fi
    SRC_COUNT=${#SRC_GLOB[@]}
    echo "  uploading $SRC_COUNT source archives to ${STAGING_DIR}/sources/..."
    rsync -av --mkpath \
        "${SRC_GLOB[@]}" \
        "$STAGING_RSYNC/sources/" \
        || { echo "ERROR: source archive rsync failed" >&2; exit 1; }
    echo "  OK — $SRC_COUNT source archives uploaded to sources/"
fi

# Step 4: Atomic promote — directory swap (M1 fix, owner-picked option b)
# Since M2 already creates a per-invocation timestamped staging dir,
# the promote is a single atomic rename: _staging-YYYYMMDDTHHMMSSZ/ → live/
# within the same filesystem (POSIX mv is atomic for directories).
# Clients during the swap see EITHER the old complete dir OR the new
# complete dir — never a mixed state.
echo "[4/4] Promoting ${STAGING_DIR} → live (atomic directory swap)..."
ssh "${REMOTE_USER}@${REMOTE_HOST}" bash -s -- \
    "$REMOTE_PATH" "$STAGING_DIR" << 'SSHEOF' || { echo "ERROR: atomic promote failed" >&2; exit 1; }
set -e -o pipefail
LIVE="$1"
STAGING_DIR="$2"
STAGING="$LIVE/$STAGING_DIR"

if [ ! -d "$STAGING" ]; then
    echo "ERROR: staging directory not found on remote: $STAGING" >&2
    exit 1
fi

# Verify staging has the minimum required files
if [ ! -f "$STAGING/InterGenOS.db" ] || [ ! -f "$STAGING/InterGenOS.db.sig" ]; then
    echo "ERROR: staging directory missing index or signature" >&2
    exit 1
fi

# Atomic promote: symlink-swap pattern (rename-the-symlink-not-the-directory).
# The symlink always points at a valid target; no 404 window for clients.
# 1. Point current.new at the new staging dir (staging is already complete)
# 2. Atomically rename current.new over current
# 3. Archive the now-unreferenced old snapshot (if any) — no rush, clients
#    are already fetching from the new staging dir through current/
PREVIOUS=$(readlink -f "$LIVE/current" 2>/dev/null || echo "")

ln -sfn "$STAGING_DIR" "$LIVE/current.new"
mv -T "$LIVE/current.new" "$LIVE/current"
echo "  current → ${STAGING_DIR}/ symlink swapped"

# Archive the prior snapshot now that clients are on the new one
if [ -n "$PREVIOUS" ] && [ -d "$PREVIOUS" ]; then
    ARCHIVE_TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
    mkdir -p "$LIVE/_previous"
    mv "$PREVIOUS" "$LIVE/_previous/${STAGING_DIR}-prev-${ARCHIVE_TIMESTAMP}"
    echo "  Archived previous snapshot → _previous/"
fi

echo "Publish complete: $(date -u)"
echo "Packages: $(ls "$LIVE/$STAGING_DIR"/*.igos.tar.gz 2>/dev/null | wc -l)"
echo "Index size: $(stat -c%s "$LIVE/$STAGING_DIR/InterGenOS.db") bytes"
SSHEOF
echo "  OK — promoted via current/ symlink swap"

# Step 5: Transparency log append (L-024)
# Push the signed index + signature to the InterGenOS transparency-log git
# repo with a structured commit message. Git provides the append-only
# property (master branch is force-push-protected on the remote); every
# clone of the repo gives an independent verifier the full publication
# history. Audit row L-024 explicitly proposed this shape ("publish each
# signed index's sha256 to public append-only log; git repo with
# co-maintainer pushes works; rekor is the proper tool"). Rekor v2
# integration is queued as v1.1 enhancement — adds a second attestation
# target alongside this git log, doesn't replace it.
echo "[5/5] Transparency log append..."
if [ "$SKIP_TRANSPARENCY" = true ]; then
    echo "  SKIP — --skip-transparency flag set; this publish is NOT in the"
    echo "         append-only transparency log. Re-publish with the flag"
    echo "         removed before considering the snapshot fully attested."
else
    if [ ! -d "$TRANSPARENCY_LOCAL/.git" ]; then
        echo "  cloning transparency-log repo to $TRANSPARENCY_LOCAL..."
        git clone --depth 100 "$TRANSPARENCY_GIT_REMOTE" "$TRANSPARENCY_LOCAL" \
            || { echo "ERROR: transparency-log clone failed ($TRANSPARENCY_GIT_REMOTE)" >&2; exit 1; }
    else
        git -C "$TRANSPARENCY_LOCAL" pull --ff-only origin master \
            || { echo "ERROR: transparency-log pull failed (likely diverged remote — investigate before next publish)" >&2; exit 1; }
    fi

    LOG_DIR="$TRANSPARENCY_LOCAL/x86_64/current"
    mkdir -p "$LOG_DIR"
    cp "$INDEX_PATH" "$LOG_DIR/InterGenOS.db"
    cp "$SIG_PATH"   "$LOG_DIR/InterGenOS.db.sig"

    INDEX_SHA=$(sha256sum "$INDEX_PATH" | awk '{print $1}')
    SIG_SHA=$(sha256sum "$SIG_PATH" | awk '{print $1}')
    INDEX_SIZE=$(stat -c%s "$INDEX_PATH")
    SIG_SIZE=$(stat -c%s "$SIG_PATH")
    PREV_ENTRY=$(git -C "$TRANSPARENCY_LOCAL" log -1 --format=%H 2>/dev/null || echo INIT)

    COMMIT_MSG=$(cat <<EOFCM
publish: ${STAGING_DIR} InterGenOS.db transparency-log entry

x86_64/current/InterGenOS.db
  sha256 = ${INDEX_SHA}
  size   = ${INDEX_SIZE}

x86_64/current/InterGenOS.db.sig
  sha256 = ${SIG_SHA}
  size   = ${SIG_SIZE}

signed-by-fingerprint = ${GPG_FP}
prev-entry            = ${PREV_ENTRY}
log-version           = 1
EOFCM
)
    git -C "$TRANSPARENCY_LOCAL" add x86_64/current/InterGenOS.db x86_64/current/InterGenOS.db.sig
    if git -C "$TRANSPARENCY_LOCAL" diff --cached --quiet; then
        echo "  WARN — transparency-log working tree showed no changes; skipping commit"
        echo "         (this snapshot may have been previously logged — investigate)"
    else
        git -C "$TRANSPARENCY_LOCAL" commit -m "$COMMIT_MSG" \
            || { echo "ERROR: transparency-log commit failed" >&2; exit 1; }
        git -C "$TRANSPARENCY_LOCAL" push origin master \
            || { echo "ERROR: transparency-log push failed (resolve + push manually before next publish)" >&2; exit 1; }
        NEW_ENTRY=$(git -C "$TRANSPARENCY_LOCAL" log -1 --format=%H)
        echo "  OK — logged at $TRANSPARENCY_GIT_REMOTE entry=${NEW_ENTRY} prev=${PREV_ENTRY}"
    fi
fi

echo ""
echo "=== Publish Complete ==="
echo "Repository:    https://repo.intergenos.org/x86_64/current/"
echo "Index:         InterGenOS.db ($(stat -c%s "$INDEX_PATH") bytes)"
echo "Signature:     InterGenOS.db.sig ($(stat -c%s "$SIG_PATH") bytes)"
echo "Packages:      $COUNT published"
if [ "$SKIP_TRANSPARENCY" != true ]; then
    echo "Transparency:  $TRANSPARENCY_GIT_REMOTE master HEAD"
fi
