#!/bin/bash
# mirror-publish.sh — package mirror publish workflow for InterGenOS.
#
# Runs on the build-VM or developer host that produced a set of .igos.tar.gz
# archives. Generates the InterGenOS.db index from those archives, signs it
# with the master GPG key, rsyncs the staged tree to the fleet user's home
# directory on the VPS, and prints the exact promote command that the
# VPS-admin agent (root + cPanel-account write access) runs to atomic-flip
# the live mirror snapshot.
#
# Does NOT have or require write access to the cPanel account's docroot.
# The staging dir lives under \$VPS_FLEET_USER's home on the VPS (env vars
# below); the atomic-promote step is a VPS-admin-side action.
#
# See docs/mirror/design.md for the full design rationale.

set -euo pipefail

# ---- Defaults --------------------------------------------------------------

ARCHIVES_DIR="${ARCHIVES_DIR:-/var/lib/igos/archives}"
ARCH="${ARCH:-x86_64}"
GPG_KEY_FP="${GPG_KEY_FP:-5597A3E0587B253006D0DD7B8C50826182083050}"
VPS_FLEET_USER="${VPS_FLEET_USER:-christopher}"
VPS_SSH_HOST="${VPS_SSH_HOST:-origin.intergenstudios.com}"
VPS_SSH_TARGET="${VPS_SSH_TARGET:-${VPS_FLEET_USER}@${VPS_SSH_HOST}}"
VPS_SSH_PORT="${VPS_SSH_PORT:-2200}"
VPS_STAGING_BASE="${VPS_STAGING_BASE:-/home/${VPS_FLEET_USER}/mirror-staging}"
LOCAL_STAGING_DIR="${LOCAL_STAGING_DIR:-}"   # auto-derived if empty

DRY_RUN="${DRY_RUN:-0}"

# ---- Helpers ---------------------------------------------------------------

log() { printf '[mirror-publish] %s\n' "$*" >&2; }
die() { printf '[mirror-publish] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<EOF
Usage: $0 [--dry-run]

Reads .igos.tar.gz files from \$ARCHIVES_DIR (default $ARCHIVES_DIR),
generates the signed mirror index, stages to VPS, prints the promote
command.

Env vars (with defaults shown):
  ARCHIVES_DIR=$ARCHIVES_DIR
  ARCH=$ARCH
  GPG_KEY_FP=$GPG_KEY_FP
  VPS_SSH_TARGET=$VPS_SSH_TARGET
  VPS_SSH_PORT=$VPS_SSH_PORT
  VPS_STAGING_BASE=$VPS_STAGING_BASE

Flags:
  --dry-run    skip rsync and the promote-command echo; just stage
               locally and report what would be sent

Prereqs:
  - python3 + pkm/repo.py importable from \$REPO_ROOT
  - gpg with the master signing key available (or via NK#1 PIV slot 9c)
  - ssh access to \$VPS_SSH_TARGET on port \$VPS_SSH_PORT
EOF
}

for arg in "$@"; do
    case "$arg" in
        -h|--help) usage; exit 0;;
        --dry-run) DRY_RUN=1;;
        *) die "unknown arg: $arg (try --help)";;
    esac
done

# ---- Pre-checks ------------------------------------------------------------

[ -d "$ARCHIVES_DIR" ] || die "archives dir not found: $ARCHIVES_DIR"
shopt -s nullglob
mapfile -t archives < <(printf '%s\n' "$ARCHIVES_DIR"/*.igos.tar.gz | sort)
[ "${#archives[@]}" -gt 0 ] || die "no .igos.tar.gz files in $ARCHIVES_DIR"
log "found ${#archives[@]} archive(s) in $ARCHIVES_DIR"

command -v gpg >/dev/null || die "gpg not found in PATH"
gpg --list-secret-keys "$GPG_KEY_FP" >/dev/null 2>&1 \
    || die "GPG signing key $GPG_KEY_FP not available (locally or via NK PIV)"

command -v rsync >/dev/null || die "rsync not found in PATH"
command -v python3 >/dev/null || die "python3 not found in PATH"

REPO_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"/.. && pwd)"
[ -f "$REPO_ROOT/pkm/repo.py" ] || die "pkm/repo.py not found at $REPO_ROOT/pkm/repo.py"

if [ "$DRY_RUN" -eq 0 ]; then
    ssh -p "$VPS_SSH_PORT" -o ConnectTimeout=8 -o BatchMode=yes "$VPS_SSH_TARGET" \
        'true' >/dev/null 2>&1 \
        || die "ssh to $VPS_SSH_TARGET:$VPS_SSH_PORT failed (check key + agent-rules access)"
fi

# ---- Staging ---------------------------------------------------------------

UTC_TS="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
if [ -z "$LOCAL_STAGING_DIR" ]; then
    LOCAL_STAGING_DIR="$(mktemp -d -t mirror-publish-XXXXXX)"
    trap 'rm -rf "$LOCAL_STAGING_DIR"' EXIT
fi

STAGE_ROOT="$LOCAL_STAGING_DIR/$UTC_TS"
STAGE_ARCH="$STAGE_ROOT/$ARCH"
mkdir -p "$STAGE_ARCH"
log "staging to $STAGE_ARCH"

# Copy archives + their per-package detached signatures (if present).
# `cp --reflink=auto` is btrfs/xfs fast-clone; falls back to regular copy
# on ext4 etc. so this is portable.
for arch in "${archives[@]}"; do
    cp --reflink=auto "$arch" "$STAGE_ARCH/"
    [ -f "$arch.sig" ] && cp --reflink=auto "$arch.sig" "$STAGE_ARCH/"
done

# Ship the public key alongside so a fresh-install bootstrap can fetch it
# from the same root URL without first having to import it from a separate
# location.
log "exporting master public key to staging"
gpg --armor --export "$GPG_KEY_FP" > "$STAGE_ROOT/pubkey.asc"

# ---- Generate + sign index -------------------------------------------------

log "generating InterGenOS.db index"
PYTHONPATH="$REPO_ROOT" python3 -c "
import sys
from pathlib import Path
from pkm.repo import generate_index
out = generate_index(Path('$STAGE_ARCH'), arch='$ARCH')
print(out)
"

INDEX_PATH="$STAGE_ARCH/InterGenOS.db"
[ -f "$INDEX_PATH" ] || die "index generation did not produce $INDEX_PATH"

log "signing InterGenOS.db (touch YubiKey if prompted)"
gpg --detach-sign --armor \
    --local-user "$GPG_KEY_FP" \
    --output "$INDEX_PATH.sig" \
    "$INDEX_PATH"
[ -f "$INDEX_PATH.sig" ] || die "signing did not produce $INDEX_PATH.sig"

# ---- Local verification ----------------------------------------------------

log "verifying index signature locally"
gpg --verify "$INDEX_PATH.sig" "$INDEX_PATH" 2>&1 | grep -q 'Good signature' \
    || die "local sig verify failed — refusing to publish"

# ---- rsync to VPS ----------------------------------------------------------

if [ "$DRY_RUN" -eq 1 ]; then
    log "DRY_RUN: skipping rsync. Staged tree at: $STAGE_ROOT"
    trap - EXIT
    log "tree contents:"
    ls -la "$STAGE_ROOT" "$STAGE_ARCH" | sed 's/^/  /'
    exit 0
fi

VPS_STAGE_PATH="$VPS_STAGING_BASE/$UTC_TS"
log "rsyncing to $VPS_SSH_TARGET:$VPS_STAGE_PATH"
ssh -p "$VPS_SSH_PORT" "$VPS_SSH_TARGET" "mkdir -p '$VPS_STAGING_BASE'"
rsync -av --delete \
    -e "ssh -p $VPS_SSH_PORT" \
    "$STAGE_ROOT/" \
    "$VPS_SSH_TARGET:$VPS_STAGE_PATH/"

# ---- Print promote command -------------------------------------------------

cat <<EOF

=== Mirror staged at $VPS_SSH_TARGET:$VPS_STAGE_PATH ===

Run the following ON THE VPS as a user with write access to
/home/intergen/public_html/mirror/ (root via WHM, then su intergen).
This atomic-promotes the new snapshot to live:

    sudo -u intergen bash -c '
        set -e
        cd /home/intergen/public_html/mirror &&
        mkdir -p x86_64/_previous &&
        if [ -d x86_64/current ]; then
            mv x86_64/current x86_64/_previous/snapshot-$UTC_TS
        fi &&
        mv $VPS_STAGE_PATH/$ARCH x86_64/current &&
        cp -p $VPS_STAGE_PATH/pubkey.asc pubkey.asc 2>/dev/null || true
    '

Verify post-promote (from any external host):

    curl -sf https://intergenstudios.com/mirror/x86_64/current/InterGenOS.db \\
        -o /tmp/db && curl -sf \\
        https://intergenstudios.com/mirror/x86_64/current/InterGenOS.db.sig \\
        -o /tmp/db.sig && gpg --verify /tmp/db.sig /tmp/db

After verification, clean up the staging dir:

    rm -rf $VPS_STAGE_PATH

EOF

log "publish staging complete"
