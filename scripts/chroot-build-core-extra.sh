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

# --- Group H: Mode 3 retiered packages (BLFS dep closure, 2026-05-11) ---
# 237 packages moved from desktop/extra/ai → core to satisfy the literal
# Required + Recommended + Optional-if-in-repo cascade per the project
# dependency-enablement policy. Topologically ordered from BLFS dep db
# (build/blfs-packages.db) with data-error fixes applied via
# scripts/fix-blfs-db-data-errors.py. SCC members (GTK 15-pkg core, xdg-
# desktop-portal trio, hatch pair) are wired separately alongside their
# bootstrap variants.

run_package "abseil-cpp" "abseil-cpp" "20260107.1" \
    "abseil-cpp-20260107.1.tar.gz" \
    "Abseil C++ common libraries"

run_package "alsa-lib" "alsa-lib" "1.2.15.3" \
    "alsa-lib-1.2.15.3.tar.bz2" \
    "ALSA sound library"

run_package "aspell" "aspell" "0.60.8.2" \
    "aspell-0.60.8.2.tar.gz" \
    "Interactive spell checking program and libraries"

run_package "boost" "boost" "1.90.0" \
    "boost-1.90.0-b2-nodocs.tar.xz" \
    "C++ utility libraries"

run_package "brotli" "brotli" "1.2.0" \
    "brotli-1.2.0.tar.gz" \
    "Brotli compression library"

run_package "c-ares" "c-ares" "1.34.6" \
    "c-ares-1.34.6.tar.gz" \
    "Asynchronous DNS resolver library"

run_package "cdparanoia" "cdparanoia" "10.2" \
    "cdparanoia-III-10.2.src.tgz" \
    "CD audio extraction tool"

run_package "clucene" "clucene" "2.3.3.4" \
    "clucene-core-2.3.3.4.tar.gz" \
    "C++ port of Lucene high performance text search engine"

run_package "cracklib" "cracklib" "2.10.3" \
    "cracklib-2.10.3.tar.xz" \
    "Password checking library"

run_package "dconf" "dconf" "0.49.0" \
    "dconf-0.49.0.tar.xz" \
    "Low-level GSettings backend"

run_package "desktop-file-utils" "desktop-file-utils" "0.28" \
    "desktop-file-utils-0.28.tar.xz" \
    "Desktop file validation and installation utilities"

run_package "dosfstools" "dosfstools" "4.2" \
    "dosfstools-4.2.tar.gz" \
    "Utilities for FAT filesystems (mkfs.fat, fsck.fat)"

run_package "double-conversion" "double-conversion" "3.4.0" \
    "double-conversion-3.4.0.tar.gz" \
    "Binary-to-decimal and decimal-to-binary conversion routines for IEEE doubles"

run_package "duktape" "duktape" "2.7.0" \
    "duktape-2.7.0.tar.xz" \
    "Embeddable JavaScript engine"

run_package "fdk-aac" "fdk-aac" "2.0.3" \
    "fdk-aac-2.0.3.tar.gz" \
    "Fraunhofer FDK AAC codec"

run_package "fftw" "fftw" "3.3.11" \
    "fftw-3.3.11.tar.gz" \
    "Fastest Fourier Transform in the West (float + double precision)"

run_package "fribidi" "fribidi" "1.0.16" \
    "fribidi-1.0.16.tar.xz" \
    "Unicode Bidirectional Algorithm library"

run_package "fuse3" "fuse3" "3.18.1" \
    "fuse-3.18.1.tar.gz" \
    "Filesystem in Userspace"

run_package "giflib" "giflib" "5.2.2" \
    "giflib-5.2.2.tar.gz" \
    "GIF image library"

run_package "glad" "glad" "2.0.8" \
    "glad-2.0.8.tar.gz" \
    "OpenGL/Vulkan/EGL loader generator"

run_package "glm" "glm" "1.0.3" \
    "glm-1.0.3.tar.gz" \
    "OpenGL Mathematics — header-only C++ math library for graphics"

run_package "graphene" "graphene" "1.10.8" \
    "graphene-1.10.8.tar.xz" \
    "Graphics data types library"

run_package "graphite2" "graphite2" "1.3.14" \
    "graphite2-1.3.14.tgz" \
    "Font rendering engine for complex scripts"

run_package "gsettings-desktop-schemas" "gsettings-desktop-schemas" "49.1" \
    "gsettings-desktop-schemas-49.1.tar.xz" \
    "GSettings schemas for GNOME desktop"

run_package "at-spi2-core" "at-spi2-core" "2.58.3" \
    "at-spi2-core-2.58.3.tar.xz" \
    "Assistive Technology Service Provider Interface"

run_package "gsl" "gsl" "2.8" \
    "gsl-2.8.tar.gz" \
    "GNU Scientific Library — numerical library for C and C++"

run_package "attrs" "attrs" "25.4.0" \
    "attrs-25.4.0.tar.gz" \
    "Python classes without boilerplate"

run_package "hicolor-icon-theme" "hicolor-icon-theme" "0.18" \
    "hicolor-icon-theme-0.18.tar.xz" \
    "Default fallback icon theme"

run_package "highway" "highway" "1.3.0" \
    "highway-1.3.0.tar.gz" \
    "Performance-portable SIMD/vector intrinsics library"

run_package "hwdata" "hwdata" "0.404" \
    "hwdata-0.404.tar.gz" \
    "Hardware identification and configuration data"

run_package "icu" "icu" "78.2" \
    "icu4c-78.2-sources.tgz" \
    "International Components for Unicode"

run_package "imagemagick" "imagemagick" "7.1.2-13" \
    "ImageMagick-7.1.2-13.tar.xz" \
    "Image processing and conversion suite"

run_package "inih" "inih" "62" \
    "inih-r62.tar.gz" \
    "Simple INI file parser"

run_package "exiv2" "exiv2" "0.28.7" \
    "exiv2-0.28.7.tar.gz" \
    "Image metadata library"

run_package "iso-codes" "iso-codes" "4.20.1" \
    "iso-codes-v4.20.1.tar.gz" \
    "Country, language, and currency code lists"

run_package "jansson" "jansson" "2.15.0" \
    "jansson-2.15.0.tar.bz2" \
    "C library for encoding, decoding and manipulating JSON data"

run_package "json-glib" "json-glib" "1.10.8" \
    "json-glib-1.10.8.tar.xz" \
    "JSON parser for GLib"

run_package "libaio" "libaio" "0.3.113" \
    "libaio-0.3.113.tar.gz" \
    "Linux-native asynchronous I/O facility"

run_package "libatasmart" "libatasmart" "0.19" \
    "libatasmart-0.19.tar.xz" \
    "ATA S.M.A.R.T. disk reporting library"

run_package "libatomic_ops" "libatomic_ops" "7.10.0" \
    "libatomic_ops-7.10.0.tar.gz" \
    "Atomic memory update operations library"

run_package "libcdio-paranoia" "libcdio-paranoia" "10.2+2.0.2" \
    "libcdio-paranoia-10.2+2.0.2.tar.bz2" \
    "CD paranoia library from libcdio"

run_package "libdaemon" "libdaemon" "0.14" \
    "libdaemon-0.14.tar.gz" \
    "Lightweight C library for writing UNIX daemons"

run_package "libdisplay-info" "libdisplay-info" "0.3.0" \
    "libdisplay-info-0.3.0.tar.xz" \
    "EDID and DisplayID library"

run_package "libdrm" "libdrm" "2.4.131" \
    "libdrm-2.4.131.tar.xz" \
    "Direct Rendering Manager library"

run_package "libdvdread" "libdvdread" "7.0.1" \
    "libdvdread-7.0.1.tar.xz" \
    "DVD reading library"

run_package "libdvdnav" "libdvdnav" "7.0.0" \
    "libdvdnav-7.0.0.tar.xz" \
    "DVD navigation library"

run_package "libevent" "libevent" "2.1.12" \
    "libevent-2.1.12-stable.tar.gz" \
    "Event notification library"

run_package "libexif" "libexif" "0.6.25" \
    "libexif-0.6.25.tar.bz2" \
    "EXIF metadata library"

run_package "libgpg-error" "libgpg-error" "1.59" \
    "libgpg-error-1.59.tar.bz2" \
    "GPG error code library"

run_package "libassuan" "libassuan" "3.0.2" \
    "libassuan-3.0.2.tar.bz2" \
    "GnuPG IPC library"

run_package "libgcrypt" "libgcrypt" "1.12.0" \
    "libgcrypt-1.12.0.tar.bz2" \
    "General purpose cryptographic library"

run_package "libgudev" "libgudev" "238" \
    "libgudev-238.tar.xz" \
    "GObject-based wrapper around libudev"

run_package "libksba" "libksba" "1.6.7" \
    "libksba-1.6.7.tar.bz2" \
    "X.509 and CMS library"

run_package "libmbim" "libmbim" "1.34.0" \
    "libmbim-1.34.0.tar.gz" \
    "MBIM protocol library"

run_package "libnl" "libnl" "3.12.0" \
    "libnl-3.12.0.tar.gz" \
    "Netlink protocol library suite"

run_package "libnvme" "libnvme" "1.16.1" \
    "libnvme-1.16.1.tar.gz" \
    "NVMe management library"

run_package "libogg" "libogg" "1.3.6" \
    "libogg-1.3.6.tar.xz" \
    "Ogg bitstream container library"

run_package "flac" "flac" "1.5.0" \
    "flac-1.5.0.tar.xz" \
    "Free Lossless Audio Codec"

run_package "libpng" "libpng" "1.6.55" \
    "libpng-1.6.55.tar.xz" \
    "PNG reference library"

run_package "freetype2" "freetype2" "2.14.1" \
    "freetype-2.14.1.tar.xz" \
    "FreeType font rendering library (pass 2 — with HarfBuzz)"

run_package "libpwquality" "libpwquality" "1.4.5" \
    "libpwquality-1.4.5.tar.bz2" \
    "Password quality checking library"

run_package "libqmi" "libqmi" "1.38.0" \
    "libqmi-1.38.0.tar.gz" \
    "QMI protocol library"

run_package "libsamplerate" "libsamplerate" "0.2.2" \
    "libsamplerate-0.2.2.tar.xz" \
    "Audio sample rate conversion library"

run_package "libtiff" "libtiff" "4.7.1" \
    "tiff-4.7.1.tar.gz" \
    "TIFF image library"

run_package "libunwind" "libunwind" "1.8.3" \
    "libunwind-1.8.3.tar.gz" \
    "Portable and efficient C programming interface to determine the call-chain of a program"

run_package "gstreamer" "gstreamer" "1.28.1" \
    "gstreamer-1.28.1.tar.xz" \
    "GStreamer multimedia framework"

run_package "libusb" "libusb" "1.0.29" \
    "libusb-1.0.29.tar.bz2" \
    "USB access library"

run_package "libvorbis" "libvorbis" "1.3.7" \
    "libvorbis-1.3.7.tar.xz" \
    "Vorbis audio codec library"

run_package "libxcvt" "libxcvt" "0.1.3" \
    "libxcvt-0.1.3.tar.xz" \
    "VESA CVT standard timing modelines generator"

run_package "libxml2" "libxml2" "2.15.1" \
    "libxml2-2.15.1.tar.xz" \
    "XML parsing library"

run_package "docbook-xml" "docbook-xml" "4.5" \
    "docbook-xml-4.5.zip" \
    "DocBook XML DTD"

run_package "libxmlb" "libxmlb" "0.3.25" \
    "libxmlb-0.3.25.tar.xz" \
    "Library for querying compressed XML metadata"

run_package "libyaml" "libyaml" "0.2.5" \
    "yaml-0.2.5.tar.gz" \
    "YAML 1.1 parser and emitter"

run_package "libfyaml" "libfyaml" "0.9.4" \
    "libfyaml-0.9.4.tar.gz" \
    "YAML 1.3 parser and writer"

run_package "links" "links" "2.30" \
    "links-2.30.tar.bz2" \
    "Text and graphics mode web browser"

run_package "lmdb" "lmdb" "0.9.35" \
    "LMDB_0.9.35.tar.bz2" \
    "Lightning Memory-Mapped Database"

run_package "cyrus-sasl" "cyrus-sasl" "2.1.28" \
    "cyrus-sasl-2.1.28.tar.gz" \
    "Cyrus Simple Authentication and Security Layer"

run_package "lua" "lua" "5.4.8" \
    "lua-5.4.8.tar.gz" \
    "Lightweight scripting language"

run_package "luajit" "luajit" "20260213" \
    "luajit-20260213.tar.xz" \
    "Just-In-Time compiler for Lua"

run_package "lvm2" "lvm2" "2.03.38" \
    "LVM2.2.03.38.tgz" \
    "Logical Volume Manager"

run_package "markdown" "markdown" "3.10.2" \
    "markdown-3.10.2.tar.gz" \
    "Python Markdown implementation"

run_package "mtdev" "mtdev" "1.1.7" \
    "mtdev-1.1.7.tar.bz2" \
    "Multitouch protocol translation library"

run_package "libevdev" "libevdev" "1.13.6" \
    "libevdev-1.13.6.tar.xz" \
    "Input event device wrapper library"

run_package "libei" "libei" "1.5.0" \
    "libei-1.5.0.tar.gz" \
    "Emulated Input library"

run_package "nasm" "nasm" "3.01" \
    "nasm-3.01.tar.xz" \
    "Netwide Assembler"

run_package "dav1d" "dav1d" "1.5.3" \
    "dav1d-1.5.3.tar.bz2" \
    "AV1 video decoder"

run_package "libaom" "libaom" "3.13.1" \
    "libaom-3.13.1.tar.gz" \
    "AV1 video codec reference implementation"

run_package "libjpeg-turbo" "libjpeg-turbo" "3.1.3" \
    "libjpeg-turbo-3.1.3.tar.gz" \
    "High-speed JPEG compression/decompression library"

run_package "lcms2" "lcms2" "2.18" \
    "lcms2-2.18.tar.gz" \
    "Little Color Management System"

run_package "libjxl" "libjxl" "0.11.2" \
    "libjxl-0.11.2.tar.gz" \
    "JPEG XL image format library"

run_package "libvpx" "libvpx" "1.16.0" \
    "libvpx-1.16.0.tar.gz" \
    "VP8/VP9 video codec"

run_package "nettle" "nettle" "3.10.2" \
    "nettle-3.10.2.tar.gz" \
    "Low-level cryptographic library"

run_package "gnutls" "gnutls" "3.8.12" \
    "gnutls-3.8.12.tar.xz" \
    "GNU TLS library"

run_package "glib-networking" "glib-networking" "2.80.1" \
    "glib-networking-2.80.1.tar.xz" \
    "GIO networking extensions"

run_package "npth" "npth" "1.8" \
    "npth-1.8.tar.bz2" \
    "New portable threads library"

run_package "openjpeg2" "openjpeg2" "2.5.4" \
    "openjpeg-2.5.4.tar.gz" \
    "JPEG 2000 codec library"

run_package "opus" "opus" "1.6.1" \
    "opus-1.6.1.tar.gz" \
    "Interactive speech and audio codec"

run_package "pciutils" "pciutils" "3.14.0" \
    "pciutils-3.14.0.tar.gz" \
    "PCI device listing and configuration utilities"

run_package "perl-archive-zip" "perl-archive-zip" "1.68" \
    "Archive-Zip-1.68.tar.gz" \
    "Perl module for reading and writing Zip archive files"

run_package "perl-parse-yapp" "perl-parse-yapp" "1.21" \
    "Parse-Yapp-1.21.tar.gz" \
    "Perl parser generator"

run_package "pinentry" "pinentry" "1.3.2" \
    "pinentry-1.3.2.tar.bz2" \
    "PIN/passphrase entry dialog"

run_package "pixman" "pixman" "0.46.4" \
    "pixman-0.46.4.tar.gz" \
    "Pixel manipulation library"

run_package "protobuf" "protobuf" "33.5" \
    "protobuf-33.5.tar.gz" \
    "Protocol Buffers serialization library"

run_package "pygobject3" "pygobject3" "3.54.5" \
    "pygobject-3.54.5.tar.gz" \
    "Python GObject bindings"

run_package "Mako" "Mako" "1.3.10" \
    "mako-1.3.10.tar.gz" \
    "Python template library (needed by Mesa)"

run_package "dbus-python" "dbus-python" "1.4.0" \
    "dbus-python-1.4.0.tar.xz" \
    "Python bindings for D-Bus"

run_package "docutils" "docutils" "0.22.4" \
    "docutils-0.22.4.tar.gz" \
    "Python documentation utilities"

run_package "lxml" "lxml" "6.0.2" \
    "lxml-6.0.2.tar.gz" \
    "Python XML processing library"

run_package "itstool" "itstool" "2.0.7" \
    "itstool-2.0.7.tar.bz2" \
    "ITS-based XML translation tool"

run_package "mesa" "mesa" "25.3.5" \
    "mesa-25.3.5.tar.xz" \
    "OpenGL, Vulkan, and OpenCL implementation"

run_package "glu" "glu" "9.0.3" \
    "glu-9.0.3.tar.xz" \
    "Mesa OpenGL Utility library"

run_package "libepoxy" "libepoxy" "1.5.10" \
    "libepoxy-1.5.10.tar.xz" \
    "OpenGL function pointer management library"

run_package "libva" "libva" "2.23.0" \
    "libva-2.23.0.tar.bz2" \
    "Video Acceleration API"

run_package "numpy" "numpy" "2.4.2" \
    "numpy-2.4.2.tar.gz" \
    "Fundamental package for scientific computing with Python"

run_package "rasqal" "rasqal" "0.9.33" \
    "rasqal-0.9.33.tar.gz" \
    "RDF query language library (SPARQL)"

run_package "redland" "redland" "1.0.17" \
    "redland-1.0.17.tar.gz" \
    "RDF metadata library"

run_package "rpcsvc-proto" "rpcsvc-proto" "1.4.4" \
    "rpcsvc-proto-1.4.4.tar.xz" \
    "RPC service protocol definitions"

run_package "sassc" "sassc" "3.6.2" \
    "sassc-3.6.2.tar.gz" \
    "SASS CSS preprocessor compiler"

run_package "sbc" "sbc" "2.2" \
    "sbc-2.2.tar.xz" \
    "Bluetooth SBC audio codec"

run_package "sdl2" "sdl2" "2.32.6" \
    "SDL2-2.32.6.tar.gz" \
    "Simple DirectMedia Layer 2"

run_package "libde265" "libde265" "1.0.16" \
    "libde265-1.0.16.tar.gz" \
    "Open source H.265/HEVC video decoder"

run_package "libheif" "libheif" "1.21.2" \
    "libheif-1.21.2.tar.gz" \
    "HEIF and AVIF file format decoder and encoder"

run_package "libwebp" "libwebp" "1.6.0" \
    "libwebp-1.6.0.tar.gz" \
    "WebP image format library"

run_package "mpg123" "mpg123" "1.33.4" \
    "mpg123-1.33.4.tar.bz2" \
    "MPEG audio decoder"

run_package "shared-mime-info" "shared-mime-info" "2.4" \
    "shared-mime-info-2.4.tar.gz" \
    "Core MIME type database"

run_package "soundtouch" "soundtouch" "2.4.0" \
    "soundtouch-2.4.0.tar.gz" \
    "Audio tempo/pitch processing library"

run_package "speex" "speex" "1.2.1" \
    "speex-1.2.1.tar.gz" \
    "Audio codec designed for speech compression"

run_package "libsndfile" "libsndfile" "1.2.2" \
    "libsndfile-1.2.2.tar.xz" \
    "Library for reading and writing sound files"

run_package "lame" "lame" "3.100" \
    "lame-3.100.tar.gz" \
    "MP3 encoder"

run_package "pulseaudio" "pulseaudio" "17.0" \
    "pulseaudio-17.0.tar.xz" \
    "PulseAudio sound server"

run_package "spidermonkey" "spidermonkey" "140.8.0" \
    "firefox-140.8.0esr.source.tar.xz" \
    "Mozilla SpiderMonkey JavaScript engine"

run_package "spirv-headers" "spirv-headers" "1.4.341.0" \
    "spirv-headers-1.4.341.0.tar.gz" \
    "SPIR-V headers"

run_package "spirv-tools" "spirv-tools" "1.4.341.0" \
    "spirv-tools-1.4.341.0.tar.gz" \
    "SPIR-V tools"

run_package "glslang" "glslang" "16.2.0" \
    "glslang-16.2.0.tar.gz" \
    "GLSL/HLSL to SPIR-V compiler"

run_package "shaderc" "shaderc" "2026.1" \
    "shaderc-2026.1.tar.gz" \
    "Google GLSL/HLSL to SPIR-V shader compiler"

run_package "svt-av1" "svt-av1" "4.0.1" \
    "SVT-AV1-v4.0.1.tar.bz2" \
    "SVT-based AV1 encoder"

run_package "libavif" "libavif" "1.3.0" \
    "libavif-1.3.0.tar.gz" \
    "AVIF image format library"

run_package "taglib" "taglib" "2.2" \
    "taglib-2.2.tar.gz" \
    "Library for reading and editing audio file metadata tags"

run_package "totem-pl-parser" "totem-pl-parser" "3.26.6" \
    "totem-pl-parser-3.26.6.tar.xz" \
    "Playlist parser library"

run_package "unifdef" "unifdef" "2.12" \
    "unifdef-2.12.tar.xz" \
    "Remove"

run_package "unixodbc" "unixodbc" "2.3.14" \
    "unixODBC-2.3.14.tar.gz" \
    "Open Database Connectivity (ODBC) implementation for Unix"

run_package "openldap" "openldap" "2.6.12" \
    "openldap-2.6.12.tgz" \
    "Open source LDAP directory server and client libraries"

run_package "gnupg2" "gnupg2" "2.5.17" \
    "gnupg-2.5.17.tar.bz2" \
    "GNU Privacy Guard"

run_package "gpgme" "gpgme" "2.0.1" \
    "gpgme-2.0.1.tar.bz2" \
    "GnuPG Made Easy library"

run_package "gpgmepp" "gpgmepp" "2.0.0" \
    "gpgmepp-2.0.0.tar.xz" \
    "C++ wrapper for GPGME"

run_package "upower" "upower" "1.91.1" \
    "upower-v1.91.1.tar.gz" \
    "Power management service"

run_package "util-macros" "util-macros" "1.20.2" \
    "util-macros-1.20.2.tar.xz" \
    "Xorg autotools macros"

run_package "enchant" "enchant" "2.8.15" \
    "enchant-2.8.15.tar.gz" \
    "Generic spell checking library"

run_package "gexiv2" "gexiv2" "0.14.6" \
    "gexiv2-0.14.6.tar.xz" \
    "GObject wrapper for Exiv2"

run_package "libcloudproviders" "libcloudproviders" "0.3.6" \
    "libcloudproviders-0.3.6.tar.xz" \
    "Cloud storage integration library"

run_package "libical" "libical" "3.0.20" \
    "libical-3.0.20.tar.gz" \
    "iCalendar protocol implementation"

run_package "bluez" "bluez" "5.86" \
    "bluez-5.86.tar.xz" \
    "Bluetooth protocol stack"

run_package "libpcap" "libpcap" "1.10.6" \
    "libpcap-1.10.6.tar.xz" \
    "Packet capture library"

run_package "iptables" "iptables" "1.8.12" \
    "iptables-1.8.12.tar.xz" \
    "iptables CLI compat shim — translates iptables syntax to nftables rules"

run_package "libproxy" "libproxy" "0.5.12" \
    "libproxy-0.5.12.tar.gz" \
    "Automatic proxy configuration management library"

run_package "libsecret" "libsecret" "0.21.7" \
    "libsecret-0.21.7.tar.xz" \
    "Library for accessing secrets stored in the keyring"

run_package "libsoup3" "libsoup3" "3.6.6" \
    "libsoup-3.6.6.tar.xz" \
    "HTTP client/server library for GNOME"

run_package "geocode-glib" "geocode-glib" "3.26.4" \
    "geocode-glib-3.26.4.tar.xz" \
    "Geocoding and reverse geocoding library"

run_package "libgweather" "libgweather" "4.4.4" \
    "libgweather-4.4.4.tar.xz" \
    "GNOME weather information library"

run_package "librest" "librest" "0.10.2" \
    "librest-0.10.2.tar.xz" \
    "REST web service access library"

run_package "tinysparql" "tinysparql" "3.10.1" \
    "tinysparql-3.10.1.tar.xz" \
    "RDF graph database and SPARQL query engine"

run_package "vulkan-headers" "vulkan-headers" "1.4.341.0" \
    "vulkan-headers-1.4.341.0.tar.gz" \
    "Vulkan API headers"

run_package "wayland" "wayland" "1.24.0" \
    "wayland-1.24.0.tar.xz" \
    "Wayland display server protocol"

run_package "vulkan-loader" "vulkan-loader" "1.4.341.0" \
    "vulkan-loader-1.4.341.0.tar.gz" \
    "Vulkan ICD loader"

run_package "libplacebo" "libplacebo" "7.360.0" \
    "libplacebo-7.360.0.tar.gz" \
    "GPU-accelerated image and video processing library"

run_package "wayland-protocols" "wayland-protocols" "1.47" \
    "wayland-protocols-1.47.tar.xz" \
    "Wayland protocol extensions"

run_package "gst-plugins-bad" "gst-plugins-bad" "1.28.1" \
    "gst-plugins-bad-1.28.1.tar.xz" \
    "GStreamer bad plugins"

run_package "pipewire" "pipewire" "1.6.0" \
    "pipewire-1.6.0.tar.gz" \
    "Multimedia processing framework"

run_package "wireplumber" "wireplumber" "0.5.13" \
    "wireplumber-0.5.13.tar.gz" \
    "PipeWire session manager"

run_package "woff2" "woff2" "1.0.2" \
    "woff2-1.0.2.tar.gz" \
    "Web Open Font Format 2.0 library"

run_package "x264" "x264" "20250815" \
    "x264-20250815.tar.xz" \
    "H.264/AVC video encoder"

run_package "x265" "x265" "4.1" \
    "x265_4.1.tar.gz" \
    "H.265/HEVC video encoder"

run_package "xcb-proto" "xcb-proto" "1.17.0" \
    "xcb-proto-1.17.0.tar.xz" \
    "XCB protocol descriptions"

run_package "xdg-dbus-proxy" "xdg-dbus-proxy" "0.1.6" \
    "xdg-dbus-proxy-0.1.6.tar.xz" \
    "D-Bus proxy for sandboxed applications"

run_package "xkeyboard-config" "xkeyboard-config" "2.46" \
    "xkeyboard-config-2.46.tar.xz" \
    "X Keyboard Configuration Database"

run_package "xorgproto" "xorgproto" "2025.1" \
    "xorgproto-2025.1.tar.xz" \
    "X11 protocol headers"

run_package "libXau" "libXau" "1.0.12" \
    "libXau-1.0.12.tar.xz" \
    "X11 Authorization Protocol library"

run_package "libXdmcp" "libXdmcp" "1.1.5" \
    "libXdmcp-1.1.5.tar.xz" \
    "X Display Manager Control Protocol library"

run_package "libxcb" "libxcb" "1.17.0" \
    "libxcb-1.17.0.tar.xz" \
    "X C Binding library"

run_package "libxkbcommon" "libxkbcommon" "1.13.1" \
    "libxkbcommon-1.13.1.tar.gz" \
    "Keyboard handling library"

run_package "xwayland" "xwayland" "24.1.9" \
    "xwayland-24.1.9.tar.xz" \
    "X server running as a Wayland client"

run_package "zip" "zip" "3.0" \
    "zip30.tar.gz" \
    "Info-ZIP archiver for creating ZIP archives"

run_package "lynx" "lynx" "2.9.2" \
    "lynx2.9.2.tar.bz2" \
    "Text-mode web browser"

run_package "pygments" "pygments" "2.19.2" \
    "pygments-2.19.2.tar.gz" \
    "Syntax highlighting library"

run_package "libbytesize" "libbytesize" "2.12" \
    "libbytesize-2.12.tar.gz" \
    "Library for operations with sizes in bytes"

run_package "llvm" "llvm" "21.1.8" \
    "llvm-21.1.8.src.tar.xz" \
    "LLVM compiler infrastructure"

run_package "rust" "rust" "1.95.0" \
    "rustc-1.95.0-src.tar.xz" \
    "Rust programming language"

run_package "cargo-c" "cargo-c" "0.10.20" \
    "cargo-c-0.10.20.tar.gz" \
    "Cargo C-ABI helpers for building and installing C-compatible libraries"

run_package "ruby" "ruby" "4.0.1" \
    "ruby-4.0.1.tar.xz" \
    "Ruby programming language"

run_package "docbook-xsl-nons" "docbook-xsl-nons" "1.79.2" \
    "docbook-xsl-nons-1.79.2.tar.bz2" \
    "DocBook XSL stylesheets"

run_package "libxslt" "libxslt" "1.1.45" \
    "libxslt-1.1.45.tar.xz" \
    "XSLT processor library"

run_package "appstream" "appstream" "1.1.2" \
    "AppStream-1.1.2.tar.xz" \
    "AppStream metadata handling library"

run_package "cython" "cython" "3.2.4" \
    "cython-3.2.4.tar.gz" \
    "C extensions for Python"

run_package "libseccomp" "libseccomp" "2.6.0" \
    "libseccomp-2.6.0.tar.gz" \
    "Enhanced seccomp library"

run_package "bubblewrap" "bubblewrap" "0.11.0" \
    "bubblewrap-0.11.0.tar.xz" \
    "Unprivileged sandboxing tool"

run_package "fontconfig" "fontconfig" "2.17.1" \
    "fontconfig-2.17.1.tar.xz" \
    "Font configuration and customization library"

run_package "cairo" "cairo" "1.18.4" \
    "cairo-1.18.4.tar.xz" \
    "2D graphics library"

run_package "gst-plugins-good" "gst-plugins-good" "1.28.1" \
    "gst-plugins-good-1.28.1.tar.xz" \
    "GStreamer good plugins"

run_package "libass" "libass" "0.17.4" \
    "libass-0.17.4.tar.xz" \
    "Portable subtitle renderer (ASS/SSA format)"

run_package "ffmpeg" "ffmpeg" "8.0.1" \
    "ffmpeg-8.0.1.tar.xz" \
    "Complete multimedia framework"

run_package "polkit" "polkit" "127" \
    "polkit-127.tar.gz" \
    "PolicyKit authorization toolkit"

run_package "modemmanager" "modemmanager" "1.24.2" \
    "ModemManager-1.24.2.tar.gz" \
    "Mobile broadband modem management daemon"

run_package "poppler" "poppler" "26.02.0" \
    "poppler-26.02.0.tar.xz" \
    "PDF rendering library"

run_package "localsearch" "localsearch" "3.10.2" \
    "localsearch-3.10.2.tar.xz" \
    "Filesystem indexer and metadata extractor"

run_package "samba" "samba" "4.23.5" \
    "samba-4.23.5.tar.gz" \
    "SMB/CIFS file and print server"

run_package "xmlto" "xmlto" "0.0.29" \
    "xmlto-0.0.29.tar.bz2" \
    "XML-to-format conversion tool"

run_package "xdg-utils" "xdg-utils" "1.2.1" \
    "xdg-utils-v1.2.1.tar.gz" \
    "Desktop integration utilities"

run_package "ghostscript" "ghostscript" "10.06.0" \
    "ghostscript-10.06.0.tar.xz" \
    "Interpreter for the PostScript language and PDF"

run_package "avahi" "avahi" "0.8" \
    "avahi-0.8.tar.gz" \
    "Service Discovery for Linux using mDNS/DNS-SD"

run_package "gcr" "gcr" "3.41.2" \
    "gcr-3.41.2.tar.xz" \
    "GLib crypto and PKCS#11 framework (GTK3 version)"

run_package "gnome-keyring" "gnome-keyring" "48.0" \
    "gnome-keyring-48.0.tar.xz" \
    "GNOME password and secret storage"

run_package "gspell" "gspell" "1.14.2" \
    "gspell-1.14.2.tar.xz" \
    "Spell checking library for GTK applications"

run_package "libcanberra" "libcanberra" "0.30" \
    "libcanberra-0.30.tar.xz" \
    "XDG sound theme and event sounds library"

run_package "libhandy1" "libhandy1" "1.8.3" \
    "libhandy-1.8.3.tar.xz" \
    "GTK3 adaptive widget library"

run_package "doxygen" "doxygen" "1.16.1" \
    "doxygen-1.16.1.src.tar.gz" \
    "Documentation generation tool from annotated sources"

run_package "gtk4" "gtk4" "4.20.3" \
    "gtk-4.20.3.tar.xz" \
    "GTK 4 widget toolkit"

run_package "adwaita-icon-theme" "adwaita-icon-theme" "49.0" \
    "adwaita-icon-theme-49.0.tar.xz" \
    "GNOME default icon theme"

run_package "colord-gtk" "colord-gtk" "0.3.1" \
    "colord-gtk-0.3.1.tar.xz" \
    "GTK integration for colord"

run_package "evince" "evince" "48.1" \
    "evince-48.1.tar.xz" \
    "GNOME document viewer"

run_package "gcr4" "gcr4" "4.4.0.1" \
    "gcr-4.4.0.1.tar.xz" \
    "GNOME crypto and certificate library"

run_package "gjs" "gjs" "1.86.0" \
    "gjs-1.86.0.tar.xz" \
    "GNOME JavaScript bindings"

run_package "gnome-desktop" "gnome-desktop" "44.5" \
    "gnome-desktop-44.5.tar.xz" \
    "GNOME desktop core library"

run_package "gtksourceview5" "gtksourceview5" "5.18.0" \
    "gtksourceview-5.18.0.tar.xz" \
    "Source code editing widget for GTK4"

run_package "json-c" "json-c" "0.18" \
    "json-c-0.18-nodoc.tar.gz" \
    "JSON library for C"

run_package "cryptsetup" "cryptsetup" "2.8.4" \
    "cryptsetup-2.8.4.tar.xz" \
    "Transparent disk encryption using the kernel crypto API"

run_package "libadwaita1" "libadwaita1" "1.8.4" \
    "libadwaita-1.8.4.tar.xz" \
    "GTK4 adaptive widgets library"

run_package "gnome-online-accounts" "gnome-online-accounts" "3.56.4" \
    "gnome-online-accounts-3.56.4.tar.xz" \
    "GNOME online accounts service"

run_package "libblockdev" "libblockdev" "3.4.0" \
    "libblockdev-3.4.0.tar.gz" \
    "Library for manipulating block devices"

run_package "libinput" "libinput" "1.31.0" \
    "libinput-1.31.0.tar.gz" \
    "Input device management and event handling library"

run_package "libnotify" "libnotify" "0.8.8" \
    "libnotify-0.8.8.tar.xz" \
    "Desktop notification library"

run_package "geoclue2" "geoclue2" "2.8.0" \
    "geoclue-2.8.0.tar.gz" \
    "D-Bus geolocation service"

run_package "ibus" "ibus" "1.5.33" \
    "ibus-1.5.33.tar.gz" \
    "Intelligent Input Bus framework"

run_package "libportal" "libportal" "0.9.1" \
    "libportal-0.9.1.tar.xz" \
    "Flatpak portal library"

run_package "udisks2" "udisks2" "2.11.1" \
    "udisks-2.11.1.tar.bz2" \
    "Disk management D-Bus service"

run_package "gvfs" "gvfs" "1.58.2" \
    "gvfs-1.58.2.tar.xz" \
    "GNOME virtual filesystem"

run_package "webkitgtk-gtk3" "webkitgtk-gtk3" "2.50.5" \
    "webkitgtk-2.50.5.tar.xz" \
    "Web content engine for GTK (GTK-3 version)"

run_package "evolution-data-server" "evolution-data-server" "3.58.3" \
    "evolution-data-server-3.58.3.tar.xz" \
    "Calendar and contacts data server"

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
