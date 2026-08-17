#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# InterGenOS Compute Tier Build — opt-in GPU compute SDKs and engine variants
# Runs INSIDE the chroot after the ai tier completes.
#
# Uses igos-build (Python builder) for dependency resolution and build
# ordering. Packages in this tier are mirror-only (iso_include defaults to
# false for tier: compute): GPU compute stacks (ROCm subset, CUDA toolkit)
# and the llama.cpp engine variants built against them. They never ship on
# the ISO — installed systems pull them from the mirror on demand.
#
# GPU targets are DECLARED, never detected: the chroot has no GPU, so
# target-sensitive packages (rocblas, llama-cpp-hip) carry an explicit
# gpu_targets declaration in their package.yml.
#
# Usage:
#   sudo bash /mnt/intergenos/scripts/chroot-enter.sh \
#        /mnt/intergenos/scripts/chroot-build-compute.sh

set +h
set -e
umask 022

IGOS_SOURCES=/sources
IGOS_LOGS=/mnt/intergenos/build/logs
IGOS_JOBS=$(nproc)

mkdir -p "$IGOS_LOGS"

COMPUTE_LOG="$IGOS_LOGS/compute-build-$(date '+%Y%m%d-%H%M%S').log"

# Source the forensic-trace bash companion (no-op when IGOS_BUILD_DEBUG_VERBOSE
# unset). Inherits IGOS_TRACE_RUNID + IGOS_TRACE_START_TS from the orchestrator's
# exported env so this tier's events join the same per-build trail.
# shellcheck disable=SC1091
[ -f /mnt/intergenos/scripts/lib/trace.sh ] && source /mnt/intergenos/scripts/lib/trace.sh
if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
    trace_init "tier-compute"
    _COMPUTE_TIER_START_MS=$(date +%s%3N)
    trace_event tier_start tier=compute log_file="$COMPUTE_LOG"
    # Close the sink + emit tier_end on every exit path (success or failure).
    _compute_trace_exit() {
        local rc=$?
        local end_ms duration_ms
        end_ms=$(date +%s%3N)
        duration_ms=$((end_ms - _COMPUTE_TIER_START_MS))
        trace_event tier_end tier=compute rc::=$rc duration_ms::=$duration_ms
        trace_close
        return $rc
    }
    trap _compute_trace_exit EXIT
fi

# Shared build-output library — one house style across the shell pipeline.
# This tier's log() keeps its own sinks (tee to the tier log, trace mirror).
# shellcheck source=lib/logging.sh
[ -f /mnt/intergenos/scripts/lib/logging.sh ] && source /mnt/intergenos/scripts/lib/logging.sh

log() {
    echo "[$(igos_timestamp)] $*" | tee -a "$COMPUTE_LOG"
    # Structured peer event for cross-tool grep + jq joins. No-op when trace is off.
    if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
        trace_event tier_narration tier=compute text="$*"
    fi
}

log ""
log ">>> InterGenOS compute tier build"
log "    opt-in GPU compute SDKs and engine variants (mirror-only)"
log "    start: $(date)"
log "    cores: ${IGOS_JOBS}"
log ""

# ============================================================================
# Step 1: Verify Python dependencies for igos-build
# ============================================================================

if ! python3 -c "import yaml" 2>/dev/null; then
    log "error: PyYAML missing — Chapter 8 build is incomplete or corrupt"
    exit 1
fi

# ============================================================================
# Step 2: Run igos-build for compute tier
# ============================================================================

log "--- Running igos-build for compute tier ---"
log ""

cd /mnt/intergenos

if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
    trace_run --tee "$COMPUTE_LOG" --intent "igos-build dispatch for compute tier" \
        python3 igos-build.py \
            --build \
            --tracked \
            --skip-built \
            --tier compute \
            --sources-dir "$IGOS_SOURCES"
    BUILD_RC=$?
else
    python3 igos-build.py \
        --build \
        --tracked \
        --skip-built \
        --tier compute \
        --sources-dir "$IGOS_SOURCES" \
        2>&1 | tee -a "$COMPUTE_LOG"
    BUILD_RC=${PIPESTATUS[0]}
fi

if [ $BUILD_RC -ne 0 ]; then
    log ""
    log "error: compute tier build failed (exit $BUILD_RC)"
    log "    Check logs in $IGOS_LOGS/"
    log "    Fix the failing package, then re-run this script."
    log "    --skip-built will resume from where it left off."
    exit $BUILD_RC
fi

# ============================================================================
# Summary
# ============================================================================

TOTAL_TRACKED=$(ls /var/lib/igos/packages/ 2>/dev/null | wc -l)

log ""
log ">>> compute tier build complete"
log "    Total tracked packages: ${TOTAL_TRACKED}"
log "    end: $(date)"
log ""
