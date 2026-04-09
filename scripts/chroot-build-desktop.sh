#!/bin/bash
# InterGenOS Desktop Build — 337 packages for GNOME on Wayland
# Runs INSIDE the chroot after core, config, core-extra, and kernel complete.
#
# Handles all prerequisites automatically:
#   1. Installs PyYAML for the Python builder
#   2. Builds base-tier dependencies needed by desktop packages
#   3. Runs igos-build with --skip-built for safe restarts
#
# Usage:
#   sudo bash /mnt/intergenos/scripts/chroot-enter.sh \
#        /mnt/intergenos/scripts/chroot-build-desktop.sh

set +h
set -e
set -o pipefail
umask 022

IGOS_SOURCES=/sources
IGOS_LOGS=/mnt/intergenos/build/logs
IGOS_JOBS=$(nproc)

mkdir -p "$IGOS_LOGS"

DESKTOP_LOG="$IGOS_LOGS/desktop-build-$(date '+%Y%m%d-%H%M%S').log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$DESKTOP_LOG"
}

log ""
log "============================================"
log "  InterGenOS Desktop Build"
log "  337 packages for GNOME on Wayland"
log "  Start: $(date)"
log "  Cores: ${IGOS_JOBS}"
log "============================================"
log ""

# ============================================================================
# Step 1: Verify Python dependencies for igos-build
# ============================================================================
# PyYAML is installed as a Chapter 8 system package (alongside setuptools).
# If it's missing, the core build is broken — fail hard, don't try to fix it.

log "--- Verifying Python dependencies for igos-build ---"

if ! python3 -c "import yaml" 2>/dev/null; then
    log "ERROR: PyYAML missing — Chapter 8 build is incomplete or corrupt"
    log "       PyYAML must be installed as a core system package."
    exit 1
fi

log "  Python: $(python3 --version 2>&1)"
log "  PyYAML: $(python3 -c 'import yaml; print(yaml.__version__)')"

# ============================================================================
# Step 2: Build base-tier prerequisites needed by desktop packages
# ============================================================================

log ""
log "--- Building base-tier prerequisites ---"

cd /mnt/intergenos

# These base packages are build dependencies for desktop packages
# but aren't part of the desktop tier. Build them first.
BASE_DEPS="libtirpc popt which"

for dep in $BASE_DEPS; do
    if [ -f "/var/lib/igos/packages/${dep}-"* ] 2>/dev/null; then
        log "  $dep: already tracked — skipping"
    else
        log "  $dep: building..."
        python3 igos-build.py \
            --build --tracked --only "$dep" \
            --sources-dir "$IGOS_SOURCES" \
            2>&1 | tee -a "$DESKTOP_LOG"

        if [ ${PIPESTATUS[0]} -ne 0 ]; then
            log "ERROR: Failed to build base dependency: $dep"
            exit 1
        fi
        log "  $dep: done"
    fi
done

log "  Base prerequisites complete"

# ============================================================================
# Step 3: Run igos-build for desktop tier
# ============================================================================

log ""
log "--- Running igos-build for desktop tier ---"
log ""

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
    log "!!! Fix the failing package, then re-run this script."
    log "!!! --skip-built will resume from where it left off."
    exit $BUILD_RC
fi

# ============================================================================
# Step 4: Apply InterGenOS desktop branding
# ============================================================================

log ""
log "--- Applying InterGenOS desktop branding ---"

# Install all gsettings overrides (theme, extensions, branding)
for override in /mnt/intergenos/config/gsettings/*.gschema.override; do
    if [ -f "$override" ]; then
        install -v -m644 "$override" /usr/share/glib-2.0/schemas/
        log "  installed $(basename "$override")"
    fi
done
glib-compile-schemas /usr/share/glib-2.0/schemas/
log "  gsettings overrides compiled (theme, extensions, branding)"

# ============================================================================
# Summary
# ============================================================================

TOTAL_TRACKED=$(ls /var/lib/igos/packages/ 2>/dev/null | wc -l)

log ""
log "============================================"
log "  DESKTOP BUILD COMPLETE"
log "  Total tracked packages: ${TOTAL_TRACKED}"
log "  End: $(date)"
log "============================================"
