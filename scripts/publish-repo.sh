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
# Prerequisites:
#   - Packages built and archived at /var/lib/igos/archives/
#   - SSH key auth to origin.intergenstudios.com configured
#   - NK#1 (or NK#2) release key available to GPG
set -e -o pipefail

ARCHIVE_DIR="/var/lib/igos/archives"
REMOTE_USER="intergenos"
REMOTE_HOST="origin.intergenstudios.com"
REMOTE_PATH="/home/intergenos/repo/x86_64"
STAGING_PATH="${REMOTE_PATH}/_staging"
GPG_KEY="NK1"
DRY_RUN=false

# Release key fingerprints
declare -A GPG_KEY_FPS
GPG_KEY_FPS[NK1]="D7AA641D81ACD690C5AD865E7276E14DD8886BFE"
GPG_KEY_FPS[NK2]="81DD223F9BA9B3F2AFFBFC5AFA24B042975F775E"
GPG_KEY_FPS[S1]="D7AA641D81ACD690C5AD865E7276E14DD8886BFE"
GPG_KEY_FPS[S2]="81DD223F9BA9B3F2AFFBFC5AFA24B042975F775E"

usage() {
    echo "Usage: $0 [--dry-run] [--archive-dir DIR] [--gpg-key NK1|NK2]"
    exit 1
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)      DRY_RUN=true; shift ;;
        --archive-dir)  ARCHIVE_DIR="$2"; shift 2 ;;
        --gpg-key)      GPG_KEY="$2"; shift 2 ;;
        -h|--help)      usage ;;
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
    echo "  $ARCHIVE_DIR/*.igos.tar.gz  →  $REMOTE_HOST:$STAGING_PATH/packages/"
    echo "  $INDEX_PATH                 →  $REMOTE_HOST:$STAGING_PATH/InterGenOS.db"
    echo "  $SIG_PATH                   →  $REMOTE_HOST:$STAGING_PATH/InterGenOS.db.sig"
    echo "  Then: mv $STAGING_PATH/* → $REMOTE_PATH/ (atomic)"
    exit 0
fi

# Step 3: Rsync to staging on remote
echo "[3/4] Uploading to staging..."
STAGING_RSYNC="ssh://${REMOTE_USER}@${REMOTE_HOST}${STAGING_PATH}"
rsync -av --mkpath \
    "$ARCHIVE_DIR"/*.igos.tar.gz \
    "$INDEX_PATH" \
    "$SIG_PATH" \
    "$STAGING_RSYNC/" \
    || { echo "ERROR: rsync to staging failed" >&2; exit 1; }
echo "  OK — packages + index + signature uploaded"

# Step 4: Atomic promote from staging to live
echo "[4/4] Promoting staging → live..."
ssh "${REMOTE_USER}@${REMOTE_HOST}" << 'SSHEOF' || { echo "ERROR: atomic promote failed" >&2; exit 1; }
STAGING="/home/intergenos/repo/x86_64/_staging"
LIVE="/home/intergenos/repo/x86_64"

if [ ! -d "$STAGING" ]; then
    echo "ERROR: staging directory not found on remote" >&2
    exit 1
fi

# Archive previous index for rollback
if [ -f "$LIVE/InterGenOS.db" ]; then
    TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
    mkdir -p "$LIVE/_previous"
    cp "$LIVE/InterGenOS.db" "$LIVE/_previous/InterGenOS-${TIMESTAMP}.db"
    cp "$LIVE/InterGenOS.db.sig" "$LIVE/_previous/InterGenOS-${TIMESTAMP}.db.sig"
fi

mv "$STAGING"/*.igos.tar.gz "$LIVE/" 2>/dev/null || true
mv "$STAGING"/InterGenOS.db "$LIVE/"
mv "$STAGING"/InterGenOS.db.sig "$LIVE/"
rmdir "$STAGING" 2>/dev/null || true

echo "Publish complete: $(date -u)"
echo "Packages: $(ls "$LIVE"/*.igos.tar.gz 2>/dev/null | wc -l)"
echo "Index size: $(stat -c%s "$LIVE/InterGenOS.db") bytes"
SSHEOF
echo "  OK — promoted to live"

echo ""
echo "=== Publish Complete ==="
echo "Repository: https://repo.intergenos.org/x86_64/"
echo "Index:      InterGenOS.db ($(stat -c%s "$INDEX_PATH") bytes)"
echo "Signature:  InterGenOS.db.sig ($(stat -c%s "$SIG_PATH") bytes)"
echo "Packages:   $COUNT published"
