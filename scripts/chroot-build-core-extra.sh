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

        # Clean and extract (helper in pkg-functions.sh handles .zip / .lz /
        # .tar.* via extension dispatch)
        rm -rf "$workdir"
        mkdir -pv "$workdir"
        extract_source "${tarball}" "$workdir" || {
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

run_package "icu" "icu" "78.2" \
    "icu4c-78.2-sources.tgz" \
    "International Components for Unicode"

run_package "libxml2" "libxml2" "2.15.1" \
    "libxml2-2.15.1.tar.xz" \
    "XML parsing library"

run_package "nghttp2" "nghttp2" "1.68.1" \
    "nghttp2-1.68.1.tar.xz" \
    "HTTP/2 C library"

run_package "nspr" "nspr" "4.38.2" \
    "nspr-4.38.2.tar.gz" \
    "Netscape Portable Runtime"

# --- Group B-crypto-pre: libgpg-error + libgcrypt ---
# Moved earlier (from below glib2-bootstrap) because libxslt declares
# libgcrypt as a build dep (crypto extensions for xsl:cipher). With docbook
# + libxslt now relocated before linux-pam, the libgcrypt chain has to come
# even earlier. Scan A enforces this ordering.

run_package "libgpg-error" "libgpg-error" "1.59" \
    "libgpg-error-1.59.tar.bz2" \
    "GPG error code library"

run_package "libgcrypt" "libgcrypt" "1.12.0" \
    "libgcrypt-1.12.0.tar.bz2" \
    "General purpose cryptographic library"

# --- Group B-extra: XML/XSL doc-processing chain ---
# Needed by linux-pam (man pages via meson xmllint+RelaxNG), glib2-bootstrap,
# and other downstream consumers. The xmllint --nonet --relaxng URL → local
# file mapping requires docbook-xml + docbook-xsl-nons to be installed BEFORE
# the consuming package configures. Moved from below linux-pam to here per
# Build #9 halt at linux-pam meson:doc/man/meson.build:42 (2026-05-12).

run_package "docbook-xml" "docbook-xml" "4.5" \
    "docbook-xml-4.5.zip" \
    "DocBook XML DTD"

run_package "libxslt" "libxslt" "1.1.45" \
    "libxslt-1.1.45.tar.xz" \
    "XSLT processor library"

run_package "docbook-xsl-nons" "docbook-xsl-nons" "1.79.2" \
    "docbook-xsl-nons-1.79.2.tar.bz2" \
    "DocBook XSL stylesheets"

# --- Group C: PAM + sudo ---

run_package "libtirpc" "libtirpc" "1.3.7" \
    "libtirpc-1.3.7.tar.bz2" \
    "Transport-Independent RPC library"

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

run_package "gobject-introspection-pass1" "gobject-introspection-pass1" "1.86.0" \
    "gobject-introspection-1.86.0.tar.xz" \
    "GObject type introspection framework (bootstrap — no cairo, no doctool)"

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

run_package "patchelf" "patchelf" "0.18.0" \
    "patchelf-0.18.0.tar.gz" \
    "RPATH/dynamic-section rewriter for ELF binaries"

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

# lua: moved up from line ~685 so it builds before rpm (which 4.18 hard-
# requires via PKG_CHECK_MODULES). Caught by Scan A.2 post-Build-#9 halt.
run_package "lua" "lua" "5.4.8" \
    "lua-5.4.8.tar.gz" \
    "Lightweight scripting language"

run_package "rpm" "rpm" "4.18.2" \
    "rpm-4.18.2.tar.bz2" \
    "RPM package manager — provides rpm2cpio for shim-signed extraction"

run_package "mokutil" "mokutil" "0.7.2" \
    "mokutil-0.7.2.tar.gz" \
    "Tool for managing Machine Owner Keys (MOK) for Secure Boot"

# --- Group H: corrected tier:core set (Rule 1 + cascade-complete, 2026-05-11) ---
# 99 packages emitted in topological order based on declared
# dependencies.build (authoritative source). After the 227-row tier
# correction batch + 4 new -pass1 bootstrap variants + spurious-dep yml
# audits, this is the full set of tier:core packages that need a
# run_package call in this script. Generated by /tmp/rewire-group-h.py.

run_package "abseil-cpp" "abseil-cpp" "20260107.1" \
    "abseil-cpp-20260107.1.tar.gz" \
    "Abseil C++ common libraries"

run_package "brotli" "brotli" "1.2.0" \
    "brotli-1.2.0.tar.gz" \
    "Brotli compression library"

run_package "c-ares" "c-ares" "1.34.6" \
    "c-ares-1.34.6.tar.gz" \
    "Asynchronous DNS resolver library"

run_package "cpio" "cpio" "2.15" \
    "cpio-2.15.tar.bz2" \
    "GNU cpio - copies files into or out of archives"

run_package "cracklib" "cracklib" "2.10.3" \
    "cracklib-2.10.3.tar.xz" \
    "Password checking library"

run_package "dosfstools" "dosfstools" "4.2" \
    "dosfstools-4.2.tar.gz" \
    "Utilities for FAT filesystems (mkfs.fat, fsck.fat)"

run_package "duktape" "duktape" "2.7.0" \
    "duktape-2.7.0.tar.xz" \
    "Embeddable JavaScript engine"

run_package "editables" "editables" "0.5" \
    "editables-0.5.tar.gz" \
    "Python editable installs helper"

run_package "efibootmgr" "efibootmgr" "18" \
    "efibootmgr-18.tar.gz" \
    "Tool for managing UEFI boot entries"

run_package "fuse3" "fuse3" "3.18.1" \
    "fuse-3.18.1.tar.gz" \
    "Filesystem in Userspace"

run_package "go" "go" "1.26.2" \
    "go1.26.2.linux-amd64.tar.gz" \
    "The Go programming language compiler and toolchain"

run_package "highway" "highway" "1.3.0" \
    "highway-1.3.0.tar.gz" \
    "Performance-portable SIMD/vector intrinsics library"

run_package "iso-codes" "iso-codes" "4.20.1" \
    "iso-codes-v4.20.1.tar.gz" \
    "Country, language, and currency code lists"

run_package "iucode-tool" "iucode-tool" "2.3.1" \
    "iucode-tool-v2.3.1.tar.gz" \
    "Intel processor microcode management tool"

run_package "intel-ucode" "intel-ucode" "20250211" \
    "microcode-20250211.tar.gz" \
    "Intel CPU microcode firmware"

run_package "jansson" "jansson" "2.15.0" \
    "jansson-2.15.0.tar.bz2" \
    "C library for encoding, decoding and manipulating JSON data"

run_package "json-c" "json-c" "0.18" \
    "json-c-0.18-nodoc.tar.gz" \
    "JSON library for C"

run_package "libaio" "libaio" "0.3.113" \
    "libaio-0.3.113.tar.gz" \
    "Linux-native asynchronous I/O facility"

run_package "libatasmart" "libatasmart" "0.19" \
    "libatasmart-0.19.tar.xz" \
    "ATA S.M.A.R.T. disk reporting library"

run_package "libassuan" "libassuan" "3.0.2" \
    "libassuan-3.0.2.tar.bz2" \
    "GnuPG IPC library"

run_package "vala-pass1" "vala-pass1" "0.56.18" \
    "vala-0.56.18.tar.xz" \
    "Vala compiler (bootstrap — without valadoc/graphviz)"

run_package "libksba" "libksba" "1.6.7" \
    "libksba-1.6.7.tar.bz2" \
    "X.509 and CMS library"

run_package "libmnl" "libmnl" "1.0.5" \
    "libmnl-1.0.5.tar.bz2" \
    "Minimalistic Netlink library"

run_package "libndp" "libndp" "1.9" \
    "libndp-1.9.tar.gz" \
    "Neighbor Discovery Protocol library"

run_package "libnftnl" "libnftnl" "1.2.9" \
    "libnftnl-1.2.9.tar.xz" \
    "Netfilter nftables userspace library"

run_package "libnl" "libnl" "3.12.0" \
    "libnl-3.12.0.tar.gz" \
    "Netlink protocol library suite"

run_package "libnvme" "libnvme" "1.16.1" \
    "libnvme-1.16.1.tar.gz" \
    "NVMe management library"

run_package "libpwquality" "libpwquality" "1.4.5" \
    "libpwquality-1.4.5.tar.bz2" \
    "Password quality checking library"

run_package "libusb" "libusb" "1.0.29" \
    "libusb-1.0.29.tar.bz2" \
    "USB access library"

run_package "libpcap-pass1" "libpcap-pass1" "1.10.6" \
    "libpcap-1.10.6.tar.xz" \
    "Packet capture library (bootstrap — without Bluetooth capture)"

run_package "libyaml" "libyaml" "0.2.5" \
    "yaml-0.2.5.tar.gz" \
    "YAML 1.1 parser and emitter"

run_package "libfyaml" "libfyaml" "0.9.4" \
    "libfyaml-0.9.4.tar.gz" \
    "YAML 1.3 parser and writer"

run_package "linux-firmware" "linux-firmware" "20260309" \
    "linux-firmware-20260309.tar.xz" \
    "Firmware files for Linux kernel drivers (WiFi, GPU, audio, etc.)"

run_package "linux-kernel-pass2" "linux-kernel-pass2" "6.18.10" \
    "linux-6.18.10.tar.xz" \
    "Linux kernel (pass 2 — rebuild with merged config fragments)"

run_package "llvm" "llvm" "21.1.8" \
    "llvm-21.1.8.src.tar.xz" \
    "LLVM compiler infrastructure"

run_package "lmdb" "lmdb" "0.9.35" \
    "LMDB_0.9.35.tar.bz2" \
    "Lightning Memory-Mapped Database"

run_package "cyrus-sasl" "cyrus-sasl" "2.1.28" \
    "cyrus-sasl-2.1.28.tar.gz" \
    "Cyrus Simple Authentication and Security Layer"

# lua moved earlier (before rpm at ~line 518) so it builds before rpm's
# PKG_CHECK_MODULES check. See comment at lua's new position.

run_package "luajit" "luajit" "20260213" \
    "luajit-20260213.tar.xz" \
    "Just-In-Time compiler for Lua"

run_package "nasm" "nasm" "3.01" \
    "nasm-3.01.tar.xz" \
    "Netwide Assembler"

run_package "nettle" "nettle" "3.10.2" \
    "nettle-3.10.2.tar.gz" \
    "Low-level cryptographic library"

run_package "slang-pass1" "slang-pass1" "2.3.3" \
    "slang-2.3.3.tar.bz2" \
    "S-Lang programming library (bootstrap — without PNG image rendering)"

run_package "newt" "newt" "0.52.25" \
    "newt-0.52.25.tar.gz" \
    "Text mode windowing toolkit"

run_package "nftables" "nftables" "1.1.3" \
    "nftables-1.1.3.tar.xz" \
    "Netfilter nftables packet filtering framework"

run_package "npth" "npth" "1.8" \
    "npth-1.8.tar.bz2" \
    "New portable threads library"

run_package "pathspec" "pathspec" "1.0.4" \
    "pathspec-1.0.4.tar.gz" \
    "Utility library for gitignore style pattern matching"

run_package "pciutils" "pciutils" "3.14.0" \
    "pciutils-3.14.0.tar.gz" \
    "PCI device listing and configuration utilities"

run_package "pinentry-pass1" "pinentry-pass1" "1.3.2" \
    "pinentry-1.3.2.tar.bz2" \
    "PIN/passphrase entry dialog (bootstrap — TTY/curses only, no GNOME frontend)"

run_package "protobuf" "protobuf" "33.5" \
    "protobuf-33.5.tar.gz" \
    "Protocol Buffers serialization library"

run_package "pyproject-metadata" "pyproject-metadata" "0.11.0" \
    "pyproject_metadata-0.11.0.tar.gz" \
    "PEP 621 metadata class with core metadata generation"

run_package "meson_python" "meson_python" "0.19.0" \
    "meson_python-0.19.0.tar.gz" \
    "Python build backend (PEP 517) for Meson projects"

run_package "rpcsvc-proto" "rpcsvc-proto" "1.4.4" \
    "rpcsvc-proto-1.4.4.tar.xz" \
    "RPC service protocol definitions"

run_package "rust" "rust" "1.95.0" \
    "rustc-1.95.0-src.tar.xz" \
    "Rust programming language"

run_package "cargo-c" "cargo-c" "0.10.20" \
    "cargo-c-0.10.20.tar.gz" \
    "Cargo C-ABI helpers for building and installing C-compatible libraries"

run_package "cbindgen" "cbindgen" "0.29.2" \
    "cbindgen-0.29.2.tar.gz" \
    "C bindings generator for Rust"

run_package "ruby" "ruby" "4.0.1" \
    "ruby-4.0.1.tar.xz" \
    "Ruby programming language"

run_package "cython" "cython" "3.2.4" \
    "cython-3.2.4.tar.gz" \
    "C extensions for Python"

run_package "docutils" "docutils" "0.22.4" \
    "docutils-0.22.4.tar.gz" \
    "Python documentation utilities"

run_package "json-glib" "json-glib" "1.10.8" \
    "json-glib-1.10.8.tar.xz" \
    "JSON parser for GLib"

run_package "libseccomp" "libseccomp" "2.6.0" \
    "libseccomp-2.6.0.tar.gz" \
    "Enhanced seccomp library"

run_package "gnutls" "gnutls" "3.8.12" \
    "gnutls-3.8.12.tar.xz" \
    "GNU TLS library"

run_package "lxml" "lxml" "6.0.2" \
    "lxml-6.0.2.tar.gz" \
    "Python XML processing library"

run_package "itstool" "itstool" "2.0.7" \
    "itstool-2.0.7.tar.bz2" \
    "ITS-based XML translation tool"

run_package "openldap" "openldap" "2.6.12" \
    "openldap-2.6.12.tgz" \
    "Open source LDAP directory server and client libraries"

# mitkrb depends on keyutils + e2fsprogs + cracklib + openldap. Earlier
# ordering kept mitkrb adjacent to keyutils, but mitkrb's configure with
# --with-cracklib + --with-ldap (added during the Build #7→#8 transition
# to close silent-feature-loss flagged 2026-05-08) hard-fails when either
# library is absent. Build #8 halted at this ordering bug 2026-05-11.
# Topological order requires cracklib (line ~512) AND openldap (above)
# to land first; gating the move on the later prerequisite is openldap.
run_package "mitkrb" "mitkrb" "1.22.2" \
    "krb5-1.22.2.tar.gz" \
    "MIT Kerberos V5 authentication"

run_package "gnupg2" "gnupg2" "2.5.17" \
    "gnupg-2.5.17.tar.bz2" \
    "GNU Privacy Guard"

run_package "gpgme" "gpgme" "2.0.1" \
    "gpgme-2.0.1.tar.bz2" \
    "GnuPG Made Easy library"

run_package "gpgmepp" "gpgmepp" "2.0.0" \
    "gpgmepp-2.0.0.tar.xz" \
    "C++ wrapper for GPGME"

run_package "polkit" "polkit" "127" \
    "polkit-127.tar.gz" \
    "PolicyKit authorization toolkit"

run_package "pyyaml-pass2" "pyyaml-pass2" "6.0.3" \
    "pyyaml-6.0.3.tar.gz" \
    "PyYAML (pass 2 — rebuild with Cython/libyaml C extension)"

run_package "pycparser" "pycparser" "2.22" \
    "pycparser-2.22.tar.gz" \
    "C parser in Python (for cffi)"

run_package "cffi" "cffi" "1.17.1" \
    "cffi-1.17.1.tar.gz" \
    "Python C FFI (for python-cryptography)"

run_package "setuptools-rust" "setuptools-rust" "1.10.2" \
    "setuptools_rust-1.10.2.tar.gz" \
    "Setuptools Rust extension plugin"

run_package "python-cryptography" "python-cryptography" "44.0.0" \
    "cryptography-44.0.0.tar.gz" \
    "Python cryptographic primitives (for systemd ukify)"

run_package "python-pefile" "python-pefile" "2024.8.26" \
    "pefile-2024.8.26.tar.gz" \
    "Python PE file reader (for systemd ukify)"

run_package "rust-bindgen" "rust-bindgen" "0.72.1" \
    "rust-bindgen-0.72.1.tar.gz" \
    "Rust FFI bindings generator"

run_package "setuptools-scm" "setuptools-scm" "9.2.2" \
    "setuptools_scm-9.2.2.tar.gz" \
    "Setuptools SCM plugin"

run_package "pluggy" "pluggy" "1.6.0" \
    "pluggy-1.6.0.tar.gz" \
    "Plugin management framework"

run_package "sgml-common" "sgml-common" "0.6.3" \
    "sgml-common-0.6.3.tgz" \
    "SGML common files"

run_package "trove-classifiers" "trove-classifiers" "2026.1.14.14" \
    "trove_classifiers-2026.1.14.14.tar.gz" \
    "Canonical trove classifiers for Python packages"

run_package "hatchling" "hatchling" "1.28.0" \
    "hatchling-1.28.0.tar.gz" \
    "Python build backend"

run_package "hatch-fancy-pypi-readme" "hatch-fancy-pypi-readme" "25.1.0" \
    "hatch_fancy_pypi_readme-25.1.0.tar.gz" \
    "Hatch plugin for fancy PyPI READMEs"

run_package "hatch-vcs" "hatch-vcs" "0.5.0" \
    "hatch_vcs-0.5.0.tar.gz" \
    "Hatch plugin for VCS version source"

run_package "pygments" "pygments" "2.19.2" \
    "pygments-2.19.2.tar.gz" \
    "Syntax highlighting library"

run_package "libbytesize" "libbytesize" "2.12" \
    "libbytesize-2.12.tar.gz" \
    "Library for operations with sizes in bytes"

run_package "unifdef" "unifdef" "2.12" \
    "unifdef-2.12.tar.xz" \
    "Remove"

run_package "util-macros" "util-macros" "1.20.2" \
    "util-macros-1.20.2.tar.xz" \
    "Xorg autotools macros"

# wayland-protocols moved to tier:desktop in 2026-05-12 (was originally
# desktop, swept to core by commit 8dc10cc's bulk move; restored). Now
# routes through chroot-build-desktop.sh via Python DAG, ordered after
# wayland per declared deps.build.

run_package "which" "which" "2.23" \
    "which-2.23.tar.gz" \
    "Utility to show the full path of commands"

run_package "lvm2" "lvm2" "2.03.38" \
    "LVM2.2.03.38.tgz" \
    "Logical Volume Manager"

run_package "cryptsetup" "cryptsetup" "2.8.4" \
    "cryptsetup-2.8.4.tar.xz" \
    "Transparent disk encryption using the kernel crypto API"

run_package "libblockdev" "libblockdev" "3.4.0" \
    "libblockdev-3.4.0.tar.gz" \
    "Library for manipulating block devices"

run_package "nodejs" "nodejs" "22.22.0" \
    "node-v22.22.0.tar.xz" \
    "JavaScript runtime built on V8"

run_package "wpa_supplicant" "wpa_supplicant" "2.11" \
    "wpa_supplicant-2.11.tar.gz" \
    "WPA/WPA2/IEEE 802.1X supplicant"

run_package "networkmanager-pass1" "networkmanager-pass1" "1.56.0" \
    "NetworkManager-1.56.0.tar.xz" \
    "Network connection manager (bootstrap — system networking only, no desktop integration)"

run_package "xmlto" "xmlto" "0.0.29" \
    "xmlto-0.0.29.tar.bz2" \
    "XML-to-format conversion tool"

run_package "xorgproto" "xorgproto" "2025.1" \
    "xorgproto-2025.1.tar.xz" \
    "X11 protocol headers"

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
