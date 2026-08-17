#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# InterGenOS Chapter 10 — Making the System Bootable
# LFS 13.0 Systemd
#
# Runs INSIDE the chroot (launched via chroot-enter.sh).
# Builds the Linux kernel (Section 10.3).
#
# NOTE: Sections 10.2 (fstab) and 10.4 (GRUB) are handled during
# image deployment, not here — they depend on the target VM's
# disk layout which isn't known at chroot build time.
#
# Usage:
#   sudo bash /mnt/intergenos/scripts/chroot-enter.sh \
#        /mnt/intergenos/scripts/chroot-build-ch10.sh

set +h
set -e
umask 022

IGOS_SOURCES=/sources
IGOS_PATCHES=/sources
IGOS_LOGS=/mnt/intergenos/build/logs
IGOS_JOBS=$(nproc)
IGOS_PACKAGES=/mnt/intergenos/packages/core

export IGOS_SOURCES IGOS_PATCHES IGOS_LOGS IGOS_JOBS

mkdir -p "$IGOS_LOGS"

# Source the package tracking functions
source /mnt/intergenos/scripts/pkg-functions.sh

# Source the forensic-trace bash companion (no-op when verbose unset).
# shellcheck disable=SC1091
[ -f /mnt/intergenos/scripts/lib/trace.sh ] && source /mnt/intergenos/scripts/lib/trace.sh
if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
    trace_init "tier-ch10"
    _CH10_TIER_START_MS=$(date +%s%3N)
    trace_event tier_start tier=ch10 log_file="$IGOS_LOGS/ch10-build.log"
    _ch10_trace_exit() {
        local rc=$?
        trace_event tier_end tier=ch10 rc::=$rc duration_ms::=$(( $(date +%s%3N) - _CH10_TIER_START_MS ))
        trace_close
        return $rc
    }
    trap _ch10_trace_exit EXIT
fi

# Shared build-output library — one house style across the shell pipeline.
# This tier's log() keeps its own sinks (tee to the tier log, trace mirror).
# shellcheck source=lib/logging.sh
[ -f /mnt/intergenos/scripts/lib/logging.sh ] && source /mnt/intergenos/scripts/lib/logging.sh

log() {
    echo "[$(igos_timestamp)] $*" | tee -a "$IGOS_LOGS/ch10-build.log"
    if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
        trace_event tier_narration tier=ch10 text="$*"
    fi
}

# ============================================================================
# Build helper — same pattern as chroot-build-ch8.sh
# ============================================================================

build_ch10_package() {
    local pkg_dir="$1"
    local name="$2"
    local version="$3"
    local tarball="$4"
    local description="$5"

    local build_script="${IGOS_PACKAGES}/${pkg_dir}/build.sh"
    # Export the package template dir so pkg_stage's overlay_files() can deploy
    # this package's files/ tree into DESTDIR (2026-06-02 bash-driver files/
    # overlay fix). Re-set per package — no stale leak across iterations.
    export PKG_TEMPLATE_DIR="${IGOS_PACKAGES}/${pkg_dir}"
    local pkg_log="${IGOS_LOGS}/${name}-ch10-$(date '+%Y%m%d-%H%M%S').log"
    local workdir="/tmp/igos-build/${name}"

    if [ ! -f "$build_script" ]; then
        log "error: no build.sh found at $build_script"
        return 1
    fi

    log ">>> Chapter 10: ${name} ${version}"
    log "    log: ${pkg_log}"

    export PKG_VERSION="$version"

    if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
        trace_pkg_enter "$name" "$version" "ch10"
    fi

    # Clean and extract (helper in pkg-functions.sh handles .zip / .lz /
    # .tar.* via extension dispatch)
    rm -rf "$workdir"
    mkdir -pv "$workdir"
    extract_source "${tarball}" "$workdir" || {
        log "error: failed to extract ${tarball}"
        [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_pkg_exit "$name" 1 0
        return 1
    }
    cd "$workdir"

    local start=$(date +%s)
    local _pkg_start_ms _phase_start_ms _phase_dur_ms
    _pkg_start_ms=$(date +%s%3N)

    unset -f configure build check do_install post_install
    # Refresh env from /etc/profile.d/*.sh so packages installed earlier in
    # this phase (rust → /opt/rustc/bin via rustc.sh, etc.) are on PATH.
    source_profile_d
    source "$build_script"

    # --- CONFIGURE ---
    if declare -f configure > /dev/null 2>&1; then
        log "  [CONFIGURE] starting..."
        _phase_start_ms=$(date +%s%3N)
        pkg_run_phase configure "$pkg_log"
        local cfg_rc=$PKG_PHASE_RC
        _phase_dur_ms=$(( $(date +%s%3N) - _phase_start_ms ))
        [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_pkg_phase "$name" configure "$cfg_rc" "$_phase_dur_ms"
        if [ $cfg_rc -ne 0 ]; then
            log "  FAILED in configure"
            tail -20 "$pkg_log" | while IFS= read -r l; do log "    $l"; done
            pkg_trace_finish ch10 "$name" "$version" "$pkg_log" "$build_script" "$_pkg_start_ms" "$cfg_rc" configure
            return 1
        fi
        log "  [CONFIGURE] done"
    fi

    # --- BUILD ---
    if declare -f build > /dev/null 2>&1; then
        log "  [BUILD] starting..."
        _phase_start_ms=$(date +%s%3N)
        pkg_run_phase build "$pkg_log"
        local b_rc=$PKG_PHASE_RC
        _phase_dur_ms=$(( $(date +%s%3N) - _phase_start_ms ))
        [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_pkg_phase "$name" build "$b_rc" "$_phase_dur_ms"
        if [ $b_rc -ne 0 ]; then
            log "  FAILED in build"
            tail -20 "$pkg_log" | while IFS= read -r l; do log "    $l"; done
            pkg_trace_finish ch10 "$name" "$version" "$pkg_log" "$build_script" "$_pkg_start_ms" "$b_rc" build
            return 1
        fi
        log "  [BUILD] done"
    fi

    # --- INSTALL (via DESTDIR staging + package tracking) ---
    log "  [INSTALL] staging..."
    _phase_start_ms=$(date +%s%3N)
    # Bracketed (NOT `|| rc=$?`): a `||` operand suspends errexit through
    # pkg_install -> pkg_stage -> pkg_run_phase, defeating the do_install
    # subshell protection. set +e captures rc without aborting; set -e restores.
    # Release honesty — see chroot-build-ch8.sh for the full rationale. Empty
    # release => no PACKAGE RELEASE header => import leaves the recorded release
    # alone, rather than asserting a default as fact.
    local _pkg_release
    _pkg_release=$(get_package_release "${IGOS_PACKAGES}/${pkg_dir}/package.yml")
    set +e
    pkg_install "$name" "$version" "$description" "$_pkg_release"
    local i_rc=$?
    set -e
    _phase_dur_ms=$(( $(date +%s%3N) - _phase_start_ms ))
    [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_pkg_phase "$name" install "$i_rc" "$_phase_dur_ms"
    if [ $i_rc -ne 0 ]; then
        log "  FAILED in install/staging"
        pkg_trace_finish ch10 "$name" "$version" "$pkg_log" "$build_script" "$_pkg_start_ms" "$i_rc" install
        return 1
    fi

    # --- POST-INSTALL ---
    if declare -f post_install > /dev/null 2>&1; then
        log "  [POST-INSTALL] running..."
        _phase_start_ms=$(date +%s%3N)
        # Capture the package's own file hashes BEFORE the hook runs, so a
        # file it rewrites in place is recorded as hook-managed content
        # rather than left looking like damage to every later check.
        pkg_hook_baseline "$name"
        pkg_run_phase post_install "$pkg_log"
        local pi_rc=$PKG_PHASE_RC
        _phase_dur_ms=$(( $(date +%s%3N) - _phase_start_ms ))
        [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_pkg_phase "$name" post_install "$pi_rc" "$_phase_dur_ms"
        # A failing post_install must HALT LOUDLY (mirror do_install handling).
        # ch10 already used pkg_run_phase but ignored pi_rc; add the halt-check
        # so post_install failures surface rather than ship. 2026-06-02 fix.
        if [ "$pi_rc" -ne 0 ]; then
            log "  FAILED in post_install (exit $pi_rc)"
            pkg_trace_finish ch10 "$name" "$version" "$pkg_log" "$build_script" "$_pkg_start_ms" "$pi_rc" post_install
            return 1
        fi
        pkg_record_hook_changes "$name"
        log "  [POST-INSTALL] done"
    fi

    local elapsed=$(( $(date +%s) - start ))
    log "  SUCCESS: ${name} ${version} (${elapsed}s)"
    log ""

    pkg_trace_finish ch10 "$name" "$version" "$pkg_log" "$build_script" "$_pkg_start_ms" 0 all
    cd /
    rm -rf "$workdir"
    return 0
}

# ============================================================================
# Initialize
# ============================================================================

pkg_init

log ""
log ">>> InterGenOS Chapter 10 — bootable system"
log "    start: $(date)"
log "    cores: ${IGOS_JOBS}"
log ""

# ============================================================================
# 10.3: Linux Kernel
# ============================================================================

# ---- Staged-kernel hygiene: prune BEFORE building (decided
# gate wave, 2026-07-12). The module dir + vmlinuz are RELEASE-named
# (CONFIG_LOCALVERSION=-igos-<release>), so a release-bumped rebuild
# ORPHANS the prior release's tree instead of overwriting it (ge9b-02:
# the r3->r4 rebuild left 6.18.10-igos-3 beside -igos-4, caught only by
# nvidia's recipe gate). Prune every staged kernel that is not the one
# this phase is about to build, then assert exclusivity after the deploy.
# NOTE: the kernel deploy path's pkm-DB re-registration gap on
# same-version release bumps is a tracked pkm-layer item — this prune
# fixes the filesystem state; the DB fix
# lands at the pkm layer.
KVER_FILE=/mnt/intergenos/packages/core/linux-kernel/package.yml
KVER=$(grep '^version:' "$KVER_FILE" | awk '{print $2}' | tr -d '"')
KREL=$(grep '^release:' "$KVER_FILE" | awk '{print $2}' | tr -d '"')
FULL_KVER="${KVER}-igos-${KREL:-1}"
shopt -s nullglob
for _mod_tree in /usr/lib/modules/*/; do
    _mod_tree="${_mod_tree%/}"
    _staged_kver="${_mod_tree##*/}"
    if [ "$_staged_kver" != "$FULL_KVER" ]; then
        log "PRUNE: superseded staged kernel ${_staged_kver} (module tree + vmlinuz) — this phase builds ${FULL_KVER}"
        rm -rf "/usr/lib/modules/${_staged_kver}"
        rm -f "/boot/vmlinuz-${_staged_kver}"
    fi
done
shopt -u nullglob
unset _mod_tree _staged_kver

build_ch10_package "linux-kernel" "linux-kernel" "6.18.10" \
    "linux-6.18.10.tar.xz" \
    "Linux kernel" || {
    log "error: kernel build failed"
    exit 1
}

# ---- Staged-kernel exclusivity gate at the point of creation: the deploy
# just above is the only place a kernel enters the chroot, so assert
# exactly-one HERE — a --stop-after kernel run or a standalone systemd-run
# of this driver exits through this check, never leaving a twin latent for
# a later phase entry to find.
bash /mnt/intergenos/scripts/preflight-single-kernel.sh --expect "$FULL_KVER" || {
    log "error: staged-kernel exclusivity gate failed after kernel deploy"
    exit 1
}

# ============================================================================
# Summary
# ============================================================================

log ""
log ">>> Chapter 10 build complete"
log ""
log "    Kernel installed to /boot/vmlinuz-${version}-igos-<release> (release per linux-kernel/package.yml)"
log "    Modules installed to /lib/modules/6.18.10"
log ""
log "    note: /etc/fstab and GRUB configuration will be"
log "    completed during image deployment to the target VM."
