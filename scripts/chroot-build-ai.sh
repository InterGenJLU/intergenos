#!/bin/bash
# InterGenOS AI Tier Build — InterGen assistant and dependencies
# Runs INSIDE the chroot after desktop tier completes.
#
# Uses igos-build (Python builder) for dependency resolution and build
# ordering. Packages in this tier provide the InterGen AI assistant.
#
# Usage:
#   sudo bash /mnt/intergenos/scripts/chroot-enter.sh \
#        /mnt/intergenos/scripts/chroot-build-ai.sh

set +h
set -e
umask 022

IGOS_SOURCES=/sources
IGOS_LOGS=/mnt/intergenos/build/logs
IGOS_JOBS=$(nproc)

mkdir -p "$IGOS_LOGS"

AI_LOG="$IGOS_LOGS/ai-build-$(date '+%Y%m%d-%H%M%S').log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$AI_LOG"
}

log ""
log "============================================"
log "  InterGenOS AI Tier Build"
log "  InterGen assistant and dependencies"
log "  Start: $(date)"
log "  Cores: ${IGOS_JOBS}"
log "============================================"
log ""

# ============================================================================
# Step 1: Verify Python dependencies for igos-build
# ============================================================================

if ! python3 -c "import yaml" 2>/dev/null; then
    log "ERROR: PyYAML missing — Chapter 8 build is incomplete or corrupt"
    exit 1
fi

# ============================================================================
# Step 2: Run igos-build for ai tier
# ============================================================================

log "--- Running igos-build for AI tier ---"
log ""

cd /mnt/intergenos

python3 igos-build.py \
    --build \
    --tracked \
    --skip-built \
    --tier ai \
    --sources-dir "$IGOS_SOURCES" \
    2>&1 | tee -a "$AI_LOG"

BUILD_RC=${PIPESTATUS[0]}

if [ $BUILD_RC -ne 0 ]; then
    log ""
    log "!!! AI tier build failed (exit $BUILD_RC)"
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
log "  AI TIER BUILD COMPLETE"
log "  Total tracked packages: ${TOTAL_TRACKED}"
log "  End: $(date)"
log "============================================"
log ""
log "  To set up InterGen:"
log "    intergen model download --auto   # Download AI model for your hardware"
log "    intergen status                  # Check assistant status"
log ""
