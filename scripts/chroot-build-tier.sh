#!/bin/bash
# ==========================================================================
# InterGenOS Unified Tier Builder
#
# Runs INSIDE the chroot. Bootstraps PyYAML into the temporary Python
# (from LFS Ch. 7), then invokes the Python builder for any tier.
#
# Replaces the per-tier bash build scripts (chroot-build-ch8.sh,
# chroot-build-core-extra.sh, chroot-build-base.sh, chroot-build-desktop.sh)
# with a single entry point. One builder, one set of templates.
#
# Usage:
#   bash /mnt/intergenos/scripts/chroot-build-tier.sh --tier core
#   bash /mnt/intergenos/scripts/chroot-build-tier.sh --tier base
#   bash /mnt/intergenos/scripts/chroot-build-tier.sh --tier desktop
#
# The Python builder handles dependency resolution, build ordering,
# DESTDIR staging, manifest tracking, and skip-built logic.
# ==========================================================================

set +h
set -e
umask 022

IGOS_SOURCES=/sources
IGOS_LOGS=/var/log/igos-build
TIER=""

# --------------------------------------------------------------------------
# Parse arguments
# --------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
    case "$1" in
        --tier)
            TIER="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1"
            echo "Usage: $0 --tier <core|base|desktop>"
            exit 1
            ;;
    esac
done

if [ -z "$TIER" ]; then
    echo "ERROR: --tier argument is required"
    echo "Usage: $0 --tier <core|base|desktop>"
    exit 1
fi

mkdir -pv "$IGOS_LOGS"

TIER_LOG="${IGOS_LOGS}/${TIER}-build-$(date '+%Y%m%d-%H%M%S').log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$TIER_LOG"
}

log ""
log "============================================"
log "  InterGenOS Tier Build: ${TIER}"
log "  Start: $(date)"
log "  Cores: $(nproc)"
log "============================================"
log ""

# ==========================================================================
# Step 1: Ensure PyYAML is available for the Python builder
# ==========================================================================

log "--- Checking Python dependencies for igos-build ---"

if python3 -c "import yaml" 2>/dev/null; then
    log "  PyYAML: already installed"
else
    log "  PyYAML: not found — installing..."

    # Try pip first (may work on some Python versions)
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

    # Manual install fallback (required for Python 3.14 where pip is broken)
    if [ "${PIP_BROKEN:-}" = "true" ]; then
        # Python 3.14 ships without setuptools or distutils — bootstrap setuptools first
        if ! python3 -c "import setuptools" 2>/dev/null; then
            SETUPTOOLS_TAR=$(ls ${IGOS_SOURCES}/setuptools-*.tar.gz 2>/dev/null | head -1)
            if [ -z "$SETUPTOOLS_TAR" ]; then
                log "ERROR: No setuptools tarball found in $IGOS_SOURCES"
                exit 1
            fi
            log "  Bootstrapping setuptools from $SETUPTOOLS_TAR..."
            SETUPTOOLS_WORK=$(mktemp -d)
            tar -xf "$SETUPTOOLS_TAR" -C "$SETUPTOOLS_WORK" --strip-components=1
            cd "$SETUPTOOLS_WORK"
            python3 bootstrap/bootstrap.py 2>&1 | tail -5
            cd /
            rm -rf "$SETUPTOOLS_WORK"
            if python3 -c "import setuptools" 2>/dev/null; then
                log "  setuptools: bootstrapped"
            else
                log "ERROR: Failed to bootstrap setuptools"
                exit 1
            fi
        fi

        PYYAML_TAR=$(ls ${IGOS_SOURCES}/PyYAML-*.tar.gz ${IGOS_SOURCES}/pyyaml-*.tar.gz 2>/dev/null | head -1)
        if [ -z "$PYYAML_TAR" ]; then
            log "ERROR: No PyYAML tarball found in $IGOS_SOURCES"
            log "       Download PyYAML from https://pypi.org/project/PyYAML/"
            exit 1
        fi

        log "  Installing PyYAML from $PYYAML_TAR..."
        PYYAML_WORK=$(mktemp -d)
        tar -xf "$PYYAML_TAR" -C "$PYYAML_WORK" --strip-components=1
        cd "$PYYAML_WORK"
        python3 setup.py install 2>&1 | tail -5
        cd /
        rm -rf "$PYYAML_WORK"

        if python3 -c "import yaml" 2>/dev/null; then
            log "  PyYAML: installed manually"
        else
            log "ERROR: Failed to install PyYAML — igos-build cannot run"
            exit 1
        fi
    fi
fi

# Verify
if ! python3 -c "import yaml; print(f'PyYAML {yaml.__version__}')" 2>/dev/null; then
    log "ERROR: PyYAML import test failed"
    exit 1
fi

log "  Python: $(python3 --version 2>&1)"
log "  PyYAML: $(python3 -c 'import yaml; print(yaml.__version__)')"

# ==========================================================================
# Step 2: Run the Python builder for the requested tier
# ==========================================================================

log ""
log "--- Running igos-build for ${TIER} tier ---"
log ""

cd /mnt/intergenos

python3 igos-build.py \
    --build \
    --tracked \
    --skip-built \
    --tier "$TIER" \
    --sources-dir "$IGOS_SOURCES" \
    2>&1 | tee -a "$TIER_LOG"

BUILD_RC=${PIPESTATUS[0]}

if [ $BUILD_RC -ne 0 ]; then
    log ""
    log "!!! ${TIER^} build failed (exit $BUILD_RC)"
    log "!!! Check logs in $IGOS_LOGS/"
    exit $BUILD_RC
fi

log ""
log "============================================"
log "  ${TIER^} build complete!"
log "  End: $(date)"
log "============================================"
