#!/bin/bash
# InterGenOS Core Extra Build — additional packages beyond LFS
# Builds after Chapter 8 completes, inside the chroot.
#
# These packages were promoted from "base" to "core" because they are
# foundational libraries or build dependencies required by the build
# system and/or by many downstream packages.
#
# Groups:
#   A. TLS/Certificate chain (libtasn1, libunistring, libidn2, p11-kit, make-ca, libpsl)
#   B. Network tools (nghttp2, libssh2, curl, wget, git)
#   C. Authentication (linux-pam, sudo) + shadow rebuild
#   D. Foundational libraries (glib2, libarchive, libuv, nspr, nss)
#   E. Build infrastructure (cmake)
#
# Uses the same package tracking as Chapter 8 (pkg-functions.sh).
#
# Usage:
#   sudo bash /mnt/intergenos/scripts/chroot-enter.sh \
#        /mnt/intergenos/scripts/chroot-build-core-extra.sh
#
# To resume after a failure:
#   IGOS_START_AT=<name> sudo bash /mnt/intergenos/scripts/chroot-enter.sh \
#        /mnt/intergenos/scripts/chroot-build-core-extra.sh
#
# To rebuild only one package (surgical, no continuation), combine with
# IGOS_STOP_AFTER=<name>:
#   IGOS_START_AT=nss IGOS_STOP_AFTER=nss sudo bash chroot-enter.sh \
#        /mnt/intergenos/scripts/chroot-build-core-extra.sh

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
IGOS_PACKAGES=/mnt/intergenos/packages/core
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
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$IGOS_LOGS/core-extra-build.log"
}

# ============================================================================
# Build helper — same pattern as Chapter 8
# ============================================================================

build_core_package() {
    local pkg_dir="$1"
    local name="$2"
    local version="$3"
    local tarball="$4"
    local description="$5"

    local build_script="${IGOS_PACKAGES}/${pkg_dir}/build.sh"
    local pkg_log="${IGOS_LOGS}/${name}-core-extra-$(date '+%Y%m%d-%H%M%S').log"
    local workdir="/tmp/igos-build/${name}"

    if [ ! -f "$build_script" ]; then
        log "ERROR: No build.sh found at $build_script"
        return 1
    fi

    log "=========================================="
    log "  Core Extra: ${name} ${version}"
    log "  Log: ${pkg_log}"
    log "=========================================="

    export PKG_VERSION="$version"

    # Source-less packages (source: [] in package.yml) — typically internal
    # InterGenOS components like pkm whose source files are bind-mounted via
    # /mnt/intergenos and copied directly in do_install. Pass tarball="" to
    # signal "no extraction." Mirrors the Python builder's existing handling.
    if [ -z "$tarball" ]; then
        rm -rf "$workdir"
        mkdir -pv "$workdir"
        cd "$workdir"
        log "  [SOURCE-LESS] no tarball; using empty workdir at $workdir"
    else
        # Verify source integrity before extraction
        local expected_sha256
        expected_sha256=$(get_package_sha256 "${IGOS_PACKAGES}/${pkg_dir}/package.yml")
        if ! verify_source_checksum "${IGOS_SOURCES}/${tarball}" "$expected_sha256"; then
            log "FATAL: Source integrity check failed for ${tarball} — aborting"
            return 1
        fi

        # Clean and extract
        rm -rf "$workdir"
        mkdir -pv "$workdir"
        tar -xf "${IGOS_SOURCES}/${tarball}" -C "$workdir" --strip-components=1 \
            --no-same-owner --no-same-permissions || {
            log "ERROR: Failed to extract ${tarball}"
            return 1
        }
        cd "$workdir"
    fi

    local start=$(date +%s)

    # Apply declared patches BEFORE sourcing build.sh (parity with
    # igos-build.py's styles/base.py:_patch_commands). Helper is sourced
    # from pkg-functions.sh. Skipped for sourceless packages (no source
    # tree to patch into). mitkrb halt 2026-05-10 surfaced this gap.
    if [ -n "$tarball" ]; then
        cd "$workdir"
        if ! apply_package_patches "${IGOS_PACKAGES}/${pkg_dir}/package.yml" >> "$pkg_log" 2>&1; then
            log "  FAILED in patch-apply"
            tail -20 "$pkg_log" | while IFS= read -r l; do log "    $l"; done
            return 1
        fi
    fi

    # Clear any previously-defined functions
    unset -f configure build check do_install post_install

    # Source the package build script
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
    # package.yml (see docs/test-allow-list.md and pkg_run_tests in
    # pkg-functions.sh). Bare `|| true` in check() is forbidden.
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

    # --- INSTALL (via DESTDIR staging + package tracking) ---
    cd "$workdir"
    log "  [INSTALL] staging..."
    pkg_install "$name" "$version" "$description"
    local rc=$?
    if [ $rc -ne 0 ]; then
        log "  FAILED in install/staging (exit $rc)"
        return 1
    fi

    # --- POST-INSTALL (runs on live system if defined) ---
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

    build_core_package "$@" || {
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
# Build Order — additional core packages
# ============================================================================

log ""
log "============================================"
log "  InterGenOS Core Extra Build"
EXTRA_PKG_COUNT=$(grep -c '^run_package' "$0" 2>/dev/null || echo "?")
log "  ${EXTRA_PKG_COUNT} packages beyond LFS 13.0"
log "  Start: $(date)"
log "  Cores: ${IGOS_JOBS}"
log "============================================"
log ""

# Initialize package database (continues from Chapter 8)
pkg_init

# --- Group A: TLS/Certificate Chain foundations ---

run_package "libtasn1" "libtasn1" "4.21.0" \
    "libtasn1-4.21.0.tar.gz" \
    "ASN.1 library used by GnuTLS and p11-kit"

run_package "libunistring" "libunistring" "1.4.2" \
    "libunistring-1.4.2.tar.xz" \
    "Unicode string library for C"

# --- Group D: Foundational libraries (no deps) ---

run_package "libuv" "libuv" "1.52.1" \
    "libuv-v1.52.1.tar.gz" \
    "Multi-platform asynchronous I/O library"

run_package "libarchive" "libarchive" "3.8.6" \
    "libarchive-3.8.6.tar.xz" \
    "Multi-format archive and compression library"

run_package "nghttp2" "nghttp2" "1.68.1" \
    "nghttp2-1.68.1.tar.xz" \
    "HTTP/2 C library"

run_package "nspr" "nspr" "4.38.2" \
    "nspr-4.38.2.tar.gz" \
    "Netscape Portable Runtime"

# --- Group C: PAM + sudo ---

run_package "linux-pam" "linux-pam" "1.7.2" \
    "Linux-PAM-1.7.2.tar.xz" \
    "Pluggable Authentication Modules"

run_package "shadow-pam" "shadow-pam" "4.19.3" \
    "shadow-4.19.3.tar.xz" \
    "Shadow password suite (rebuilt with Linux-PAM support)"

# --- Group C2: OpenSSH (requires linux-pam + shadow-pam) ---

run_package "openssh" "openssh" "10.2p1" \
    "openssh-10.2p1.tar.gz" \
    "Secure Shell client and server"

# --- Group D: glib2 bootstrap (Void Linux approach) ---
# Three separate packages break the circular dependency:
#   glib2-bootstrap (no introspection) → gobject-introspection → glib2 (full)
# Each is a standard DESTDIR build. No hacks needed.

run_package "glib2-bootstrap" "glib2-bootstrap" "2.88.1" \
    "glib-2.88.1.tar.xz" \
    "GLib core library (bootstrap — without introspection)"

run_package "gobject-introspection" "gobject-introspection" "1.86.0" \
    "gobject-introspection-1.86.0.tar.xz" \
    "GObject type introspection framework"

run_package "glib2" "glib2" "2.88.1" \
    "glib-2.88.1.tar.xz" \
    "GLib core library (full — with introspection)"

# --- Group A: TLS chain (deps on libtasn1, libunistring) ---

run_package "libidn2" "libidn2" "2.3.8" \
    "libidn2-2.3.8.tar.gz" \
    "Internationalized domain names library"

run_package "p11-kit" "p11-kit" "0.26.2" \
    "p11-kit-0.26.2.tar.xz" \
    "PKCS#11 module loading library"

# --- Group C: sudo ---

run_package "sudo" "sudo" "1.9.17p2" \
    "sudo-1.9.17p2.tar.gz" \
    "Execute commands as another user"

# --- Group B: libssh2 (before curl) ---

run_package "libssh2" "libssh2" "1.11.1" \
    "libssh2-1.11.1.tar.gz" \
    "Client-side SSH2 library"

# --- Group D+E: NSS ---

run_package "nss" "nss" "3.121" \
    "nss-3.121.tar.gz" \
    "Network Security Services"

run_package "make-ca" "make-ca" "1.16.1" \
    "make-ca-1.16.1.tar.gz" \
    "CA certificate management utility"

# --- Group A: libpsl (deps on libidn2, libunistring) ---

run_package "libpsl" "libpsl" "0.21.5" \
    "libpsl-0.21.5.tar.gz" \
    "Public Suffix List library"

# --- Group B: Network tools ---

run_package "curl" "curl" "8.19.0" \
    "curl-8.19.0.tar.xz" \
    "Command line tool and library for transferring data with URLs"

run_package "wget" "wget" "1.25.0" \
    "wget-1.25.0.tar.gz" \
    "Network file retriever"

# --- Group E: Build infrastructure ---

run_package "cmake" "cmake" "4.3.1" \
    "cmake-4.3.1.tar.gz" \
    "Cross-platform build system generator"

run_package "git" "git" "2.53.0" \
    "git-2.53.0.tar.xz" \
    "Distributed version control system"

# --- Group F: Bootloader prerequisites ---
# busybox-static is required by phase_bootloader to assemble the live
# initramfs. It is statically linked so it has no chroot runtime deps and
# can be placed anywhere in the build order.

run_package "busybox-static" "busybox-static" "1.37.0" \
    "busybox-1.37.0.tar.bz2" \
    "Statically-linked busybox userland for initramfs"

# --- Group G: Core libraries previously misclassified or silent-skipped ---
# Authored 2026-05-10 to address Build #6 audit findings. Each entry below
# was either:
#   (a) silent-skipped — declared tier:core but never wired (Rulebook Rule 2)
#   (b) retiered to a non-core tier in Build #6 to bypass missing wiring
#       (Rulebook Rule 1 violation — corrected by tier-restoration)
#   (c) newly authored to provide a system library a feature-disable flag
#       had been bypassing (Rulebook Rule 3 — xxhash for rsync)
# Order is topological per declared build deps.

run_package "popt" "popt" "1.19" \
    "popt-1.19.tar.gz" \
    "Command line option parsing library"

run_package "lzo" "lzo" "2.10" \
    "lzo-2.10.tar.gz" \
    "Real-time data compression library"

run_package "xxhash" "xxhash" "0.8.3" \
    "xxhash-0.8.3.tar.gz" \
    "Extremely fast non-cryptographic hash algorithm library + xxhsum CLI"

run_package "apparmor" "apparmor" "3.1.7" \
    "apparmor-v3.1.7.tar.gz" \
    "AppArmor MAC framework — libapparmor, parser, profiles"

run_package "pkm" "pkm" "0.1.0" \
    "" \
    "InterGenOS package manager — install, remove, query, verify"

run_package "help2man" "help2man" "1.49.3" \
    "help2man-1.49.3.tar.xz" \
    "Generate man pages from --help output"

run_package "keyutils" "keyutils" "1.6.3" \
    "keyutils-1.6.3.tar.gz" \
    "Linux kernel key management utilities"

# mitkrb depends on keyutils — keep this immediately after keyutils so the
# topological order in this file matches the declared build deps. (C2 audit
# 2026-05-10 caught the prior mis-ordering: mitkrb was at @111 listing
# keyutils@115 as a build dep, would have halted on a fresh post-base chroot.)
run_package "mitkrb" "mitkrb" "1.22.2" \
    "krb5-1.22.2.tar.gz" \
    "MIT Kerberos V5 authentication"

# mandoc: BSD man-page formatter. Required by efivar's docs/Makefile to
# convert .mdoc source → traditional man pages at build time. Retiered
# desktop→core (tier reflects intrinsic nature: man-page formatter,
# analogous to groff which is core). Owner-approved 2026-05-10 after
# efivar halt surfaced the cross-tier dep cascade.
run_package "mandoc" "mandoc" "1.14.6" \
    "mandoc-1.14.6.tar.gz" \
    "BSD man page formatter and viewer"

run_package "efivar" "efivar" "39" \
    "efivar-39.tar.gz" \
    "Library and tools for EFI variable management"

run_package "gnu-efi" "gnu-efi" "3.0.18" \
    "gnu-efi-3.0.18.tar.bz2" \
    "GNU EFI development library — UEFI headers and libraries"

run_package "btrfs-progs" "btrfs-progs" "6.19.1" \
    "btrfs-progs-v6.19.1.tar.xz" \
    "Userspace utilities and headers for the Btrfs filesystem"

run_package "efitools" "efitools" "1.9.2" \
    "efitools-1.9.2.tar.gz" \
    "Tools for manipulating UEFI Secure Boot variables and keys"

run_package "sbsigntool" "sbsigntool" "0.9.5" \
    "sbsigntools-0.9.5.tar.gz" \
    "Tools for signing and verifying EFI binaries with Secure Boot keys"

run_package "rpm" "rpm" "4.18.2" \
    "rpm-4.18.2.tar.bz2" \
    "RPM package manager — provides rpm2cpio for shim-signed extraction"

run_package "mokutil" "mokutil" "0.7.2" \
    "mokutil-0.7.2.tar.gz" \
    "Tool for managing Machine Owner Keys (MOK) for Secure Boot"

# ============================================================================
# Summary
# ============================================================================

TOTAL_CORE_EXTRA=$(grep -c "^run_package" "$0" | head -1)
TOTAL_TRACKED=$(ls /var/lib/igos/packages/ 2>/dev/null | wc -l)

log ""
log "============================================"
log "  Core Extra Build Complete"
log "  Total tracked packages: ${TOTAL_TRACKED}"
log "  End: $(date)"
log "============================================"
