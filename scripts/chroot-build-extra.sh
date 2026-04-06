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
IGOS_LOGS=/mnt/intergenos/build/logs
IGOS_JOBS=$(nproc)

mkdir -p "$IGOS_LOGS"

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
    log "  PyYAML not found — installing via ensurepip + pip..."

    if ! pip3 --version 2>/dev/null; then
        python3 -m ensurepip --upgrade
        log "  pip: $(pip3 --version)"
    fi

    if ! python3 -c "import setuptools" 2>/dev/null; then
        pip3 install --no-index --find-links="${IGOS_SOURCES}" \
            --no-cache-dir --no-user setuptools
    fi

    # Ensure distutils compatibility shim is active
    SITE=$(python3 -c "import site; print(site.getsitepackages()[0])")
    if [ ! -f "$SITE/distutils-precedence.pth" ]; then
        echo "import _distutils_hack; _distutils_hack.add_shim()" > "$SITE/distutils-precedence.pth"
    fi

    pip3 install --no-index --find-links="${IGOS_SOURCES}" \
        --no-cache-dir --no-user PyYAML

    if ! python3 -c "import yaml" 2>/dev/null; then
        log "ERROR: Failed to install PyYAML"
        exit 1
    fi
    log "  PyYAML: installed"
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
