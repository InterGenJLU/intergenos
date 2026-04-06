#!/bin/bash
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

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$IGOS_LOGS/ch10-build.log"
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
    local pkg_log="${IGOS_LOGS}/${name}-ch10-$(date '+%Y%m%d-%H%M%S').log"
    local workdir="/tmp/igos-build/${name}"

    if [ ! -f "$build_script" ]; then
        log "ERROR: No build.sh found at $build_script"
        return 1
    fi

    log "=========================================="
    log "  Chapter 10: ${name} ${version}"
    log "  Log: ${pkg_log}"
    log "=========================================="

    export PKG_VERSION="$version"

    rm -rf "$workdir"
    mkdir -pv "$workdir"
    tar -xf "${IGOS_SOURCES}/${tarball}" -C "$workdir" --strip-components=1 || {
        log "ERROR: Failed to extract ${tarball}"
        return 1
    }
    cd "$workdir"

    local start=$(date +%s)

    unset -f configure build check do_install post_install
    source "$build_script"

    # --- CONFIGURE ---
    if declare -f configure > /dev/null 2>&1; then
        log "  [CONFIGURE] starting..."
        configure >> "$pkg_log" 2>&1
        if [ $? -ne 0 ]; then
            log "  FAILED in configure"
            tail -20 "$pkg_log" | while IFS= read -r l; do log "    $l"; done
            return 1
        fi
        log "  [CONFIGURE] done"
    fi

    # --- BUILD ---
    if declare -f build > /dev/null 2>&1; then
        log "  [BUILD] starting..."
        build >> "$pkg_log" 2>&1
        if [ $? -ne 0 ]; then
            log "  FAILED in build"
            tail -20 "$pkg_log" | while IFS= read -r l; do log "    $l"; done
            return 1
        fi
        log "  [BUILD] done"
    fi

    # --- INSTALL (via DESTDIR staging + package tracking) ---
    log "  [INSTALL] staging..."
    pkg_install "$name" "$version" "$description"
    if [ $? -ne 0 ]; then
        log "  FAILED in install/staging"
        return 1
    fi

    # --- POST-INSTALL ---
    if declare -f post_install > /dev/null 2>&1; then
        log "  [POST-INSTALL] running..."
        post_install >> "$pkg_log" 2>&1
        log "  [POST-INSTALL] done"
    fi

    local elapsed=$(( $(date +%s) - start ))
    log "  SUCCESS: ${name} ${version} (${elapsed}s)"
    log ""

    cd /
    rm -rf "$workdir"
    return 0
}

# ============================================================================
# Initialize
# ============================================================================

pkg_init

log ""
log "============================================"
log "  InterGenOS Chapter 10 — Bootable System"
log "  Start: $(date)"
log "  Cores: ${IGOS_JOBS}"
log "============================================"
log ""

# ============================================================================
# 10.3: Linux Kernel
# ============================================================================

build_ch10_package "linux-kernel" "linux-kernel" "6.18.10" \
    "linux-6.18.10.tar.xz" \
    "Linux kernel" || {
    log "!!! KERNEL BUILD FAILED"
    exit 1
}

# ============================================================================
# Summary
# ============================================================================

log ""
log "============================================"
log "  CHAPTER 10 BUILD COMPLETE"
log ""
log "  Kernel installed to /boot/vmlinuz-6.18.10-igos"
log "  Modules installed to /lib/modules/6.18.10"
log ""
log "  NOTE: /etc/fstab and GRUB configuration will be"
log "  completed during image deployment to the target VM."
log "============================================"
