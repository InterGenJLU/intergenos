#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
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

# Source the forensic-trace bash companion (no-op when verbose unset).
# shellcheck disable=SC1091
[ -f /mnt/intergenos/scripts/lib/trace.sh ] && source /mnt/intergenos/scripts/lib/trace.sh
if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
    trace_init "tier-extra"
    _EXTRA_TIER_START_MS=$(date +%s%3N)
    trace_event tier_start tier=extra log_file="$EXTRA_LOG"
    _extra_trace_exit() {
        local rc=$?
        trace_event tier_end tier=extra rc::=$rc duration_ms::=$(( $(date +%s%3N) - _EXTRA_TIER_START_MS ))
        trace_close
        return $rc
    }
    trap _extra_trace_exit EXIT
fi

# Shared build-output library — one house style across the shell pipeline.
# This tier's log() keeps its own sinks (tee to the tier log, trace mirror).
# shellcheck source=lib/logging.sh
[ -f /mnt/intergenos/scripts/lib/logging.sh ] && source /mnt/intergenos/scripts/lib/logging.sh

log() {
    echo "[$(igos_timestamp)] $*" | tee -a "$EXTRA_LOG"
    if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
        trace_event tier_narration tier=extra text="$*"
    fi
}

log ""
log ">>> InterGenOS extra tier build"
log "    User-facing applications"
log "    start: $(date)"
log "    cores: ${IGOS_JOBS}"
log ""

# ============================================================================
# Step 1: Verify Python dependencies for igos-build
# ============================================================================
# PyYAML is installed as a Chapter 8 system package (alongside setuptools).
# If it's missing, the core build is broken — fail hard, don't try to fix it.

if ! python3 -c "import yaml" 2>/dev/null; then
    log "error: PyYAML missing — Chapter 8 build is incomplete or corrupt"
    log "       PyYAML must be installed as a core system package."
    exit 1
fi

# ============================================================================
# Step 2: Run igos-build for extra tier
# ============================================================================

log "--- Running igos-build for extra tier ---"
log ""

cd /mnt/intergenos

if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
    trace_run --tee "$EXTRA_LOG" --intent "igos-build dispatch for extra tier" \
        python3 igos-build.py \
            --build \
            --tracked \
            --skip-built \
            --tier extra \
            --sources-dir "$IGOS_SOURCES"
    BUILD_RC=$?
else
    python3 igos-build.py \
        --build \
        --tracked \
        --skip-built \
        --tier extra \
        --sources-dir "$IGOS_SOURCES" \
        2>&1 | tee -a "$EXTRA_LOG"
    BUILD_RC=${PIPESTATUS[0]}
fi

if [ $BUILD_RC -ne 0 ]; then
    log ""
    log "error: extra tier build failed (exit $BUILD_RC)"
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
log ">>> Extra tier build complete"
log "    Total tracked packages: ${TOTAL_TRACKED}"
log "    end: $(date)"
log ""
log "  To install proprietary applications, invoke via pkm so the install"
log "  footprint is tracked (pkm files/verify/remove work as expected):"
log "    sudo pkm install chrome       # Google Chrome"
log "    sudo pkm install vscode       # Visual Studio Code"
log "    sudo pkm install claude-code  # Claude Code (CLI + extension)"
log "    sudo pkm install codex        # OpenAI Codex (CLI + extension)"
log "    sudo pkm install chatgpt      # ChatGPT desktop app (with Codex)"
log ""
