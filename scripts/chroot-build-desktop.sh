#!/bin/bash
# InterGenOS Desktop Build — 312 packages for GNOME on Wayland
# Runs INSIDE the chroot after core, config, core-extra, and base complete.
#
# Uses igos-build (Python builder) for dependency resolution and build
# ordering. Requires Python 3 and PyYAML inside the chroot.
#
# Usage:
#   sudo bash /mnt/intergenos/scripts/chroot-enter.sh \
#        /mnt/intergenos/scripts/chroot-build-desktop.sh

set +h
set -e
umask 022

IGOS_SOURCES=/sources
IGOS_LOGS=/var/log/igos-build
IGOS_JOBS=$(nproc)

mkdir -pv "$IGOS_LOGS"

DESKTOP_LOG="$IGOS_LOGS/desktop-build-$(date '+%Y%m%d-%H%M%S').log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$DESKTOP_LOG"
}

log ""
log "============================================"
log "  InterGenOS Desktop Build"
log "  Start: $(date)"
log "  Cores: ${IGOS_JOBS}"
log "============================================"
log ""

# ============================================================================
# Step 1: Ensure PyYAML is available for igos-build
# ============================================================================

log "--- Checking Python dependencies for igos-build ---"

if python3 -c "import yaml" 2>/dev/null; then
    log "  PyYAML: already installed"
else
    log "  PyYAML: not found — installing..."

    # Try pip first (may work on newer pip versions)
    if pip3 install --no-cache-dir PyYAML 2>/dev/null; then
        if python3 -c "import yaml" 2>/dev/null; then
            log "  PyYAML: installed via pip"
        else
            log "  PyYAML: pip reported success but import failed — using manual install"
            PIP_BROKEN=true
        fi
    else
        log "  PyYAML: pip failed — using manual install"
        PIP_BROKEN=true
    fi

    # Manual install fallback
    if [ "${PIP_BROKEN:-}" = "true" ]; then
        PYYAML_TAR=$(ls ${IGOS_SOURCES}/PyYAML-*.tar.gz ${IGOS_SOURCES}/pyyaml-*.tar.gz 2>/dev/null | head -1)
        if [ -z "$PYYAML_TAR" ]; then
            log "ERROR: No PyYAML tarball found in $IGOS_SOURCES"
            exit 1
        fi

        TMPDIR=$(mktemp -d)
        tar -xzf "$PYYAML_TAR" -C "$TMPDIR" --strip-components=1
        cd "$TMPDIR"
        python3 setup.py install 2>&1 | tail -5
        cd /
        rm -rf "$TMPDIR"

        if python3 -c "import yaml" 2>/dev/null; then
            log "  PyYAML: installed manually"
        else
            log "ERROR: Failed to install PyYAML — igos-build cannot run"
            exit 1
        fi
    fi
fi

# Verify igos-build can import
if ! python3 -c "import yaml; print(f'PyYAML {yaml.__version__}')" 2>/dev/null; then
    log "ERROR: PyYAML import test failed"
    exit 1
fi

log "  Python: $(python3 --version 2>&1)"
log "  PyYAML: $(python3 -c 'import yaml; print(yaml.__version__)')"

# ============================================================================
# Step 2: Run igos-build for desktop tier
# ============================================================================

log ""
log "--- Running igos-build for desktop tier ---"
log ""

cd /mnt/intergenos

python3 igos-build.py \
    --build \
    --tracked \
    --skip-built \
    --tier desktop \
    --sources-dir "$IGOS_SOURCES" \
    2>&1 | tee -a "$DESKTOP_LOG"

BUILD_RC=${PIPESTATUS[0]}

if [ $BUILD_RC -ne 0 ]; then
    log ""
    log "!!! Desktop build failed (exit $BUILD_RC)"
    log "!!! Check logs in $IGOS_LOGS/"
    exit $BUILD_RC
fi

# ============================================================================
# Summary
# ============================================================================

TOTAL_TRACKED=$(ls /var/lib/igos/packages/ 2>/dev/null | wc -l)

log ""
log "============================================"
log "  Desktop Build Complete"
log "  Total tracked packages: ${TOTAL_TRACKED}"
log "  End: $(date)"
log "============================================"
