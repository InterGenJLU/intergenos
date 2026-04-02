#!/bin/bash
# InterGenOS Base Package Build — 20 packages beyond core
# Builds after core-extra completes, inside the chroot.
#
# These are end-user tools and system services that don't need to be
# in core (not build dependencies, not foundational libraries).
#
# Uses the same package tracking as Chapter 8 (pkg-functions.sh).
#
# Usage:
#   sudo bash /mnt/intergenos/scripts/chroot-enter.sh \
#        /mnt/intergenos/scripts/chroot-build-base.sh
#
# To resume after a failure:
#   IGOS_START_AT=<name> sudo bash /mnt/intergenos/scripts/chroot-enter.sh \
#        /mnt/intergenos/scripts/chroot-build-base.sh

set +h
set -e
umask 022

# ============================================================================
# Environment
# ============================================================================

IGOS_SOURCES=/sources
IGOS_PATCHES=/sources
IGOS_LOGS=/var/log/igos-build
IGOS_JOBS=$(nproc)
IGOS_PACKAGES=/mnt/intergenos/packages/base
IGOS_START_AT="${IGOS_START_AT:-}"

export IGOS_SOURCES IGOS_PATCHES IGOS_LOGS IGOS_JOBS

mkdir -pv "$IGOS_LOGS"

# Source the package tracking functions
source /mnt/intergenos/scripts/pkg-functions.sh

# ============================================================================
# Logging
# ============================================================================

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$IGOS_LOGS/base-build.log"
}

# ============================================================================
# Build helper — same pattern as Chapter 8 and core-extra
# ============================================================================

build_base_package() {
    local pkg_dir="$1"
    local name="$2"
    local version="$3"
    local tarball="$4"
    local description="$5"

    local build_script="${IGOS_PACKAGES}/${pkg_dir}/build.sh"
    local pkg_log="${IGOS_LOGS}/${name}-base-$(date '+%Y%m%d-%H%M%S').log"
    local workdir="/tmp/igos-build/${name}"

    if [ ! -f "$build_script" ]; then
        log "ERROR: No build.sh found at $build_script"
        return 1
    fi

    log "=========================================="
    log "  Base: ${name} ${version}"
    log "  Log: ${pkg_log}"
    log "=========================================="

    export PKG_VERSION="$version"

    # Clean and extract
    rm -rf "$workdir"
    mkdir -pv "$workdir"

    # Use bsdtar for .lz archives, tar for everything else
    if [[ "$tarball" == *.lz ]]; then
        bsdtar -xf "${IGOS_SOURCES}/${tarball}" -C "$workdir" --strip-components=1 || {
            log "ERROR: Failed to extract ${tarball}"
            return 1
        }
    else
        tar -xf "${IGOS_SOURCES}/${tarball}" -C "$workdir" --strip-components=1 || {
            log "ERROR: Failed to extract ${tarball}"
            return 1
        }
    fi
    cd "$workdir"

    local start=$(date +%s)

    unset -f configure build check do_install post_install

    source "$build_script"

    # --- CONFIGURE ---
    if declare -f configure > /dev/null 2>&1; then
        log "  [CONFIGURE] starting..."
        configure >> "$pkg_log" 2>&1
        local rc=$?
        if [ $rc -ne 0 ]; then
            log "  FAILED in configure (exit $rc)"
            tail -20 "$pkg_log" | while IFS= read -r l; do log "    $l"; done
            return 1
        fi
        log "  [CONFIGURE] done"
    fi

    # --- BUILD ---
    if declare -f build > /dev/null 2>&1; then
        log "  [BUILD] starting..."
        build >> "$pkg_log" 2>&1
        local rc=$?
        if [ $rc -ne 0 ]; then
            log "  FAILED in build (exit $rc)"
            tail -20 "$pkg_log" | while IFS= read -r l; do log "    $l"; done
            return 1
        fi
        log "  [BUILD] done"
    fi

    # --- CHECK (optional) ---
    if declare -f check > /dev/null 2>&1; then
        log "  [CHECK] starting..."
        check >> "$pkg_log" 2>&1
        log "  [CHECK] done (see log for results)"
    fi

    # --- INSTALL ---
    log "  [INSTALL] staging..."
    pkg_install "$name" "$version" "$description"
    local rc=$?
    if [ $rc -ne 0 ]; then
        log "  FAILED in install/staging (exit $rc)"
        return 1
    fi

    # --- POST-INSTALL ---
    if declare -f post_install > /dev/null 2>&1; then
        log "  [POST-INSTALL] running live system hooks..."
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
# Resume support
# ============================================================================

SKIP=true
if [ -z "$IGOS_START_AT" ]; then
    SKIP=false
fi

run_package() {
    local pkg_dir="$1"
    local name="$2"

    if $SKIP; then
        if [ "$name" = "$IGOS_START_AT" ] || [ "$pkg_dir" = "$IGOS_START_AT" ]; then
            SKIP=false
            log ">>> Resuming build at: $name"
        else
            log "  Skipping: $name (resuming from $IGOS_START_AT)"
            return 0
        fi
    fi

    build_base_package "$@" || {
        log ""
        log "!!! BUILD FAILED: $name"
        log "!!! Fix the issue and re-run with: IGOS_START_AT=$name"
        log ""
        exit 1
    }
}

# ============================================================================
# Build Order — 20 base packages
#
# Dependencies that are in core (already installed) don't need to be
# listed here. Only inter-base dependencies affect ordering.
# ============================================================================

log ""
log "============================================"
log "  InterGenOS Base Package Build"
log "  20 packages"
log "  Start: $(date)"
log "  Cores: ${IGOS_JOBS}"
log "============================================"
log ""

pkg_init

# --- No-dependency packages ---

run_package "cpio" "cpio" "2.15" \
    "cpio-2.15.tar.bz2" \
    "GNU cpio — copies files into or out of archives"

run_package "ed" "ed" "1.22.5" \
    "ed-1.22.5.tar.lz" \
    "Classic UNIX line editor"

run_package "fcron" "fcron" "3.4.0" \
    "fcron-3.4.0.src.tar.gz" \
    "Periodical command scheduler"

run_package "htop" "htop" "3.4.1" \
    "htop-3.4.1.tar.xz" \
    "Interactive process viewer"

run_package "iotop" "iotop" "1.31" \
    "iotop-1.31.tar.xz" \
    "I/O monitoring tool"

run_package "libtirpc" "libtirpc" "1.3.7" \
    "libtirpc-1.3.7.tar.bz2" \
    "Transport-Independent RPC library"

run_package "pax" "pax" "20240817" \
    "paxmirabilis-20240817.tgz" \
    "POSIX standard archive utility"

run_package "perl-file-fcntllock" "perl-file-fcntllock" "0.22" \
    "File-FcntlLock-0.22.tar.gz" \
    "Perl module for file locking"

run_package "popt" "popt" "1.19" \
    "popt-1.19.tar.gz" \
    "Command line option parsing library"

run_package "screen" "screen" "5.0.1" \
    "screen-5.0.1.tar.gz" \
    "GNU Screen terminal multiplexer"

run_package "strace" "strace" "6.19" \
    "strace-6.19.tar.xz" \
    "System call tracer"

run_package "time" "time" "1.9" \
    "time-1.9.tar.gz" \
    "GNU time — resource usage summary"

run_package "which" "which" "2.23" \
    "which-2.23.tar.gz" \
    "Utility to show the full path of commands"

# --- Packages with dependencies on other base packages ---

run_package "libnsl" "libnsl" "2.0.1" \
    "libnsl-2.0.1.tar.xz" \
    "NIS library"

run_package "lsof" "lsof" "4.99.6" \
    "lsof-4.99.6.tar.gz" \
    "List open files"

run_package "rsync" "rsync" "3.4.1" \
    "rsync-3.4.1.tar.gz" \
    "Fast incremental file transfer"

run_package "atop" "atop" "2.12.1" \
    "atop-2.12.1.tar.gz" \
    "Advanced system and process monitor"

run_package "exim" "exim" "4.99.1" \
    "exim-4.99.1.tar.xz" \
    "Message Transfer Agent"

run_package "at" "at" "3.2.5" \
    "at_3.2.5.orig.tar.gz" \
    "Job scheduling commands"

run_package "btop" "btop" "1.4.6" \
    "btop-1.4.6.tar.gz" \
    "Resource monitor with TUI"

# ============================================================================
# Summary
# ============================================================================

TOTAL_TRACKED=$(ls /var/lib/igos/packages/ 2>/dev/null | wc -l)

log ""
log "============================================"
log "  Base Package Build Complete"
log "  Total tracked packages: ${TOTAL_TRACKED}"
log "  End: $(date)"
log "============================================"
