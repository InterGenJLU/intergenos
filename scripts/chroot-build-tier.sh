#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
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
IGOS_LOGS=/mnt/intergenos/build/logs
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
            echo "error: unknown argument: $1"
            echo "usage: $0 --tier <core|base|desktop>"
            exit 1
            ;;
    esac
done

if [ -z "$TIER" ]; then
    echo "error: --tier argument is required"
    echo "usage: $0 --tier <core|base|desktop>"
    exit 1
fi

mkdir -p "$IGOS_LOGS"

TIER_LOG="${IGOS_LOGS}/${TIER}-build-$(date '+%Y%m%d-%H%M%S').log"

# Source the forensic-trace bash companion (no-op when verbose unset).
# shellcheck disable=SC1091
[ -f /mnt/intergenos/scripts/lib/trace.sh ] && source /mnt/intergenos/scripts/lib/trace.sh
if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
    trace_init "tier-${TIER}"
    _T_TIER_START_MS=$(date +%s%3N)
    trace_event tier_start tier="$TIER" log_file="$TIER_LOG"
    _tier_trace_exit() {
        local rc=$?
        trace_event tier_end tier="$TIER" rc::=$rc duration_ms::=$(( $(date +%s%3N) - _T_TIER_START_MS ))
        trace_close
        return $rc
    }
    trap _tier_trace_exit EXIT
fi

# Shared build-output library — one house style across the shell pipeline.
# This tier's log() keeps its own sinks (tee to the tier log, trace mirror).
# shellcheck source=lib/logging.sh
[ -f /mnt/intergenos/scripts/lib/logging.sh ] && source /mnt/intergenos/scripts/lib/logging.sh

# The aggregated build stream — one stable path every tier appends its
# narration to, so a single `tail -f` follows a whole multi-tier build
# instead of being re-pointed at each tier handover. Resolved once, here,
# from the library so the location is decided in exactly one place. When
# the library is absent the variable stays empty and the tee below drops
# the argument, leaving this script logging exactly as it did before.
IGOS_BUILD_STREAM=""
command -v igos_build_stream_path >/dev/null 2>&1 && \
    IGOS_BUILD_STREAM="$(igos_build_stream_path)"

log() {
    echo "[$(igos_timestamp)] $*" | tee -a "$TIER_LOG" ${IGOS_BUILD_STREAM:+"$IGOS_BUILD_STREAM"}
    if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
        trace_event tier_narration tier="$TIER" text="$*"
    fi
}

log ""
log ">>> InterGenOS tier build: ${TIER}"
log "    start: $(date)"
log "    cores: $(nproc)"
log ""

# ==========================================================================
# Step 1: Verify Python dependencies for igos-build
# ==========================================================================
# PyYAML is installed as a Chapter 8 system package (alongside setuptools).
# If it's missing, the core build is broken — fail hard, don't try to fix it.

log "--- Verifying Python dependencies for igos-build ---"

if ! python3 -c "import yaml" 2>/dev/null; then
    log "error: PyYAML missing — Chapter 8 build is incomplete or corrupt"
    log "       PyYAML must be installed as a core system package."
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

if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
    trace_run --tee "$TIER_LOG" --intent "igos-build dispatch for $TIER tier" \
        python3 igos-build.py \
            --build \
            --tracked \
            --skip-built \
            --tier "$TIER" \
            --sources-dir "$IGOS_SOURCES"
    BUILD_RC=$?
else
    python3 igos-build.py \
        --build \
        --tracked \
        --skip-built \
        --tier "$TIER" \
        --sources-dir "$IGOS_SOURCES" \
        2>&1 | tee -a "$TIER_LOG"
    BUILD_RC=${PIPESTATUS[0]}
fi

if [ $BUILD_RC -ne 0 ]; then
    log ""
    log "error: ${TIER^} build failed (exit $BUILD_RC)"
    log "    Check logs in $IGOS_LOGS/"
    exit $BUILD_RC
fi

log ""
log ">>> ${TIER^} build complete"
log "    end: $(date)"
