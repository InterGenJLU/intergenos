#!/bin/bash
# InterGenOS Base Package Build — end-user tools and services beyond core
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
#
# To rebuild only one package (surgical, no continuation), combine with
# IGOS_STOP_AFTER=<name>:
#   IGOS_START_AT=htop IGOS_STOP_AFTER=htop sudo bash chroot-enter.sh \
#        /mnt/intergenos/scripts/chroot-build-base.sh

set +h
set -e
set -o pipefail
umask 022

# ============================================================================
# Environment
# ============================================================================

IGOS_SOURCES=/sources
IGOS_PATCHES=/sources
IGOS_LOGS=/mnt/intergenos/build/logs
IGOS_JOBS=$(nproc)
IGOS_PACKAGES=/mnt/intergenos/packages/base
IGOS_START_AT="${IGOS_START_AT:-}"
IGOS_STOP_AFTER="${IGOS_STOP_AFTER:-}"

export IGOS_SOURCES IGOS_PATCHES IGOS_LOGS IGOS_JOBS

mkdir -p "$IGOS_LOGS"

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

    # Clean and extract (helper in pkg-functions.sh handles .zip / .lz /
    # .tar.* via extension dispatch)
    rm -rf "$workdir"
    mkdir -pv "$workdir"
    extract_source "${tarball}" "$workdir" || {
        log "ERROR: Failed to extract ${tarball}"
        return 1
    }
    cd "$workdir"

    local start=$(date +%s)

    # Apply declared patches BEFORE sourcing build.sh (parity with
    # igos-build.py's styles/base.py:_patch_commands). Helper is sourced
    # from pkg-functions.sh. mitkrb halt 2026-05-10 surfaced this gap;
    # rsync's security_fix patch was also un-applied in prior builds.
    cd "$workdir"
    if ! apply_package_patches "${IGOS_PACKAGES}/${pkg_dir}/package.yml" >> "$pkg_log" 2>&1; then
        log "  FAILED in patch-apply"
        tail -20 "$pkg_log" | while IFS= read -r l; do log "    $l"; done
        return 1
    fi

    unset -f configure build check do_install post_install

    # Refresh env from /etc/profile.d/*.sh so packages installed earlier in
    # this phase (rust → /opt/rustc/bin via rustc.sh, etc.) are on PATH.
    source_profile_d

    source "$build_script"

    # Reset CWD before each phase. Build functions may cd into subdirectories
    # (e.g., NSS's build() does "cd nss") and bash doesn't scope cd to
    # functions — it persists into the next call. Without resetting, later
    # phases start from the wrong directory.

    # --- CONFIGURE ---
    if declare -f configure > /dev/null 2>&1; then
        cd "$workdir"
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
        cd "$workdir"
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

    # --- CHECK ---
    # Tests-as-truth: any check() failure halts the tier build. Packages with
    # known-environment-only failures opt in via the `tests:` block in
    # package.yml (see docs/test-allow-list.md). Bare `|| true` in check() is
    # forbidden.
    if declare -f check > /dev/null 2>&1; then
        cd "$workdir"
        log "  [CHECK] starting..."
        check >> "$pkg_log" 2>&1
        local rc=$?
        if [ $rc -ne 0 ]; then
            log "  FAILED in check (exit $rc)"
            tail -20 "$pkg_log" | while IFS= read -r l; do log "    $l"; done
            return 1
        fi
        log "  [CHECK] done"
    fi

    # --- INSTALL ---
    cd "$workdir"
    log "  [INSTALL] staging..."
    pkg_install "$name" "$version" "$description"
    local rc=$?
    if [ $rc -ne 0 ]; then
        log "  FAILED in install/staging (exit $rc)"
        return 1
    fi

    # --- POST-INSTALL ---
    if declare -f post_install > /dev/null 2>&1; then
        cd "$workdir"
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

    # Skip if already tracked. Mirrors chroot-build-desktop.sh's BASE_DEPS
    # pre-check: if the desktop phase pre-built cpio/libtirpc/popt/which via
    # the Python builder's --only fallback, those packages already have a
    # /var/lib/igos/packages/<name>-<version> manifest and don't need to be
    # rebuilt by this phase. Bash compgen returns 0 if any match exists.
    if compgen -G "/var/lib/igos/packages/${name}-*" > /dev/null 2>&1; then
        log "  Skipping: $name (already tracked at /var/lib/igos/packages/)"
        return 0
    fi

    build_base_package "$@" || {
        log ""
        log "!!! BUILD FAILED: $name"
        log "!!! Fix the issue and re-run with: IGOS_START_AT=$name"
        log ""
        exit 1
    }

    if [ -n "$IGOS_STOP_AFTER" ] && { [ "$name" = "$IGOS_STOP_AFTER" ] || [ "$pkg_dir" = "$IGOS_STOP_AFTER" ]; }; then
        log ""
        log ">>> Stopping after: $name (IGOS_STOP_AFTER)"
        log ""
        exit 0
    fi
}

# ============================================================================
# Build Order — base packages
#
# Dependencies that are in core (already installed) don't need to be
# listed here. Only inter-base dependencies affect ordering.
# ============================================================================

log ""
log "============================================"
log "  InterGenOS Base Package Build"
BASE_PKG_COUNT=$(grep -c '^run_package' "$0" 2>/dev/null || echo "?")
log "  ${BASE_PKG_COUNT} packages"
log "  Start: $(date)"
log "  Cores: ${IGOS_JOBS}"
log "============================================"
log ""

pkg_init

# --- No-dependency packages ---
# Note: cpio, which, popt were previously listed here but are tier:core
# (cpio + which moved 2026-05-11 per docs/package-tiers.md; popt was
# already tier:core). They are now wired in chroot-build-core-extra.sh
# and removed from this script to avoid duplicate builds.

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

# libtirpc moved to tier:core (chroot-build-core-extra.sh) 2026-05-11
# Reason: it's a system library for PAM's RPC backend, not a CLI tool.
# Original location here was a tier misclassification.

run_package "pax" "pax" "20240817" \
    "paxmirabilis-20240817.tgz" \
    "POSIX standard archive utility"

run_package "perl-file-fcntllock" "perl-file-fcntllock" "0.22" \
    "File-FcntlLock-0.22.tar.gz" \
    "Perl module for file locking"

run_package "screen" "screen" "5.0.1" \
    "screen-5.0.1.tar.gz" \
    "GNU Screen terminal multiplexer"

run_package "strace" "strace" "6.19" \
    "strace-6.19.tar.xz" \
    "System call tracer"

run_package "time" "time" "1.9" \
    "time-1.9.tar.gz" \
    "GNU time — resource usage summary"

# --- 2026-05-11: arrivals from desktop and core retiers ---

run_package "parallel" "parallel" "20260322" \
    "parallel-20260322.tar.bz2" \
    "GNU parallel — execute jobs in parallel"

run_package "rdfind" "rdfind" "1.8.0" \
    "rdfind-1.8.0.tar.gz" \
    "Duplicate-file finder"

run_package "zip" "zip" "3.0" \
    "zip30.tar.gz" \
    "Info-ZIP archiver for creating ZIP archives"

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
