#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
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

# Source the forensic-trace bash companion (no-op when verbose unset).
# shellcheck disable=SC1091
[ -f /mnt/intergenos/scripts/lib/trace.sh ] && source /mnt/intergenos/scripts/lib/trace.sh
if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
    trace_init "tier-desktop"
    _DESK_TIER_START_MS=$(date +%s%3N)
    trace_event tier_start tier=desktop log_file="$DESKTOP_LOG"
    _desk_trace_exit() {
        local rc=$?
        trace_event tier_end tier=desktop rc::=$rc duration_ms::=$(( $(date +%s%3N) - _DESK_TIER_START_MS ))
        trace_close
        return $rc
    }
    trap _desk_trace_exit EXIT
fi

# Shared build-output library — one house style across the shell pipeline.
# This tier's log() keeps its own sinks (tee to the tier log, trace mirror).
# shellcheck source=lib/logging.sh
[ -f /mnt/intergenos/scripts/lib/logging.sh ] && source /mnt/intergenos/scripts/lib/logging.sh

log() {
    echo "[$(igos_timestamp)] $*" | tee -a "$DESKTOP_LOG"
    if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
        trace_event tier_narration tier=desktop text="$*"
    fi
}

log ""
log ">>> InterGenOS desktop build"
log "    337 packages for GNOME on Wayland"
log "    start: $(date)"
log "    cores: ${IGOS_JOBS}"
log ""

# ============================================================================
# Step 1: Verify Python dependencies for igos-build
# ============================================================================
# PyYAML is installed as a Chapter 8 system package (alongside setuptools).
# If it's missing, the core build is broken — fail hard, don't try to fix it.

log "Verifying Python dependencies for igos-build…"

if ! python3 -c "import yaml" 2>/dev/null; then
    log "error: PyYAML missing — Chapter 8 build is incomplete or corrupt"
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
# cpio: required by shim-signed for `rpm2cpio | cpio -idmv` extraction
# of Fedora's MS-signed shim binary.
BASE_DEPS="cpio libtirpc popt which"

# Skip-if-tracked check uses a directory glob expand: bash globs the
# expression and `-d` tests against the first match. To avoid the
# greedy-prefix class (see chroot-build-base.sh:run_package — `at-*`
# silently matched `at-spi2-core-2.58.3`), require a digit immediately
# after the dash so we only match `<name>-<version>` patterns, not
# `<name>-<sibling-pkg-suffix>`. Versions always start with a digit.
for dep in $BASE_DEPS; do
    if compgen -G "/var/lib/igos/packages/${dep}-[0-9]*" >/dev/null 2>&1; then
        log "  $dep: already tracked — skipping"
    else
        log "  $dep: building..."
        if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
            trace_run --tee "$DESKTOP_LOG" --pkg "$dep" --intent "base prereq build for desktop tier" \
                python3 igos-build.py \
                    --build --tracked --only "$dep" \
                    --sources-dir "$IGOS_SOURCES"
            dep_rc=$?
        else
            python3 igos-build.py \
                --build --tracked --only "$dep" \
                --sources-dir "$IGOS_SOURCES" \
                2>&1 | tee -a "$DESKTOP_LOG"
            dep_rc=${PIPESTATUS[0]}
        fi
        if [ "$dep_rc" -ne 0 ]; then
            log "error: failed to build base dependency: $dep"
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

if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
    trace_run --tee "$DESKTOP_LOG" --intent "igos-build dispatch for desktop tier" \
        python3 igos-build.py \
            --build \
            --tracked \
            --skip-built \
            --tier desktop \
            --sources-dir "$IGOS_SOURCES"
    BUILD_RC=$?
else
    python3 igos-build.py \
        --build \
        --tracked \
        --skip-built \
        --tier desktop \
        --sources-dir "$IGOS_SOURCES" \
        2>&1 | tee -a "$DESKTOP_LOG"
    BUILD_RC=${PIPESTATUS[0]}
fi

if [ $BUILD_RC -ne 0 ]; then
    log ""
    log "error: desktop build failed (exit $BUILD_RC)"
    log "    Check logs in $IGOS_LOGS/"
    log "    Fix the failing package, then re-run this script."
    log "    --skip-built will resume from where it left off."
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
log ">>> Desktop build complete"
log "    Total tracked packages: ${TOTAL_TRACKED}"
log "    end: $(date)"
