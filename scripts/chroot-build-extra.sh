#!/bin/bash
# InterGenOS Extra Tier Build — User-facing applications
# Runs INSIDE the chroot after desktop tier completes.
#
# Uses igos-build (Python builder) for dependency resolution and build
# ordering. Packages in this tier are optional — the desktop works without them.
#
# Usage:
#   sudo bash /mnt/intergenos/scripts/chroot-enter.sh \
#        /mnt/intergenos/scripts/chroot-build-extra.sh

set +h
set -e
umask 022

IGOS_SOURCES=/sources
IGOS_LOGS=/var/log/igos-build
IGOS_JOBS=$(nproc)

mkdir -pv "$IGOS_LOGS"

EXTRA_LOG="$IGOS_LOGS/extra-build-$(date '+%Y%m%d-%H%M%S').log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$EXTRA_LOG"
}

log ""
log "============================================"
log "  InterGenOS Extra Tier Build"
log "  User-facing applications"
log "  Start: $(date)"
log "  Cores: ${IGOS_JOBS}"
log "============================================"
log ""

# ============================================================================
# Step 1: Ensure PyYAML is available for igos-build
# ============================================================================

if ! python3 -c "import yaml" 2>/dev/null; then
    log "  Installing PyYAML..."
    PYYAML_TAR=$(ls ${IGOS_SOURCES}/pyyaml-*.tar.gz 2>/dev/null | head -1)
    if [ -n "$PYYAML_TAR" ]; then
        TMPDIR=$(mktemp -d)
        tar -xzf "$PYYAML_TAR" -C "$TMPDIR" --strip-components=1
        cd "$TMPDIR" && python3 setup.py install 2>&1 | tail -3
        cd / && rm -rf "$TMPDIR"
    fi
fi

# ============================================================================
# Step 2: Run igos-build for extra tier
# ============================================================================

log "--- Running igos-build for extra tier ---"
log ""

cd /mnt/intergenos

python3 igos-build.py \
    --build \
    --tracked \
    --skip-built \
    --tier extra \
    --sources-dir "$IGOS_SOURCES" \
    2>&1 | tee -a "$EXTRA_LOG"

BUILD_RC=${PIPESTATUS[0]}

if [ $BUILD_RC -ne 0 ]; then
    log ""
    log "!!! Extra tier build failed (exit $BUILD_RC)"
    log "!!! Check logs in $IGOS_LOGS/"
    log "!!! Fix the failing package, then re-run this script."
    log "!!! --skip-built will resume from where it left off."
    exit $BUILD_RC
fi

# ============================================================================
# Summary
# ============================================================================

TOTAL_TRACKED=$(ls /var/lib/igos/packages/ 2>/dev/null | wc -l)

log ""
log "============================================"
log "  EXTRA TIER BUILD COMPLETE"
log "  Total tracked packages: ${TOTAL_TRACKED}"
log "  End: $(date)"
log "============================================"
log ""
log "  To install proprietary applications, run as the target user:"
log "    sudo igos-install-chrome       # Google Chrome"
log "    sudo igos-install-vscode       # Visual Studio Code"
log "    igos-install-claude-code       # Claude Code (CLI + extension)"
log ""
