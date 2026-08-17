#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
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

# Source the forensic-trace bash companion (no-op when verbose unset).
# shellcheck disable=SC1091
[ -f /mnt/intergenos/scripts/lib/trace.sh ] && source /mnt/intergenos/scripts/lib/trace.sh
if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
    trace_init "tier-core-extra"
    _CE_TIER_START_MS=$(date +%s%3N)
    trace_event tier_start tier=core-extra log_file="$IGOS_LOGS/core-extra-build.log"
    _ce_trace_exit() {
        local rc=$?
        trace_event tier_end tier=core-extra rc::=$rc duration_ms::=$(( $(date +%s%3N) - _CE_TIER_START_MS ))
        trace_close
        return $rc
    }
    trap _ce_trace_exit EXIT
fi

# ============================================================================
# Logging
# ============================================================================

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
    echo "[$(igos_timestamp)] $*" | tee -a "$IGOS_LOGS/core-extra-build.log" ${IGOS_BUILD_STREAM:+"$IGOS_BUILD_STREAM"}
    if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
        trace_event tier_narration tier=core-extra text="$*"
    fi
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
    # Export the package template dir so pkg_stage's overlay_files() can deploy
    # this package's files/ tree into DESTDIR (2026-06-02 bash-driver files/
    # overlay fix). Re-set per package — no stale leak across iterations.
    export PKG_TEMPLATE_DIR="${IGOS_PACKAGES}/${pkg_dir}"
    local pkg_log="${IGOS_LOGS}/${name}-core-extra-$(date '+%Y%m%d-%H%M%S').log"
    local workdir="/tmp/igos-build/${name}"

    if [ ! -f "$build_script" ]; then
        log "error: no build.sh found at $build_script"
        return 1
    fi

    log ">>> Core Extra: ${name} ${version}"
    log "    log: ${pkg_log}"

    export PKG_VERSION="$version"

    if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
        trace_pkg_enter "$name" "$version" "core-extra"
    fi

    # Source-less packages (source: [] in package.yml) — typically internal
    # InterGenOS components like pkm whose source files are bind-mounted via
    # /mnt/intergenos and copied directly in do_install. Pass tarball="" to
    # signal "no extraction." Mirrors the Python builder's existing handling.
    if [ -z "$tarball" ]; then
        rm -rf "$workdir"
        mkdir -pv "$workdir"
        cd "$workdir"
        log "    source-less: no tarball; using empty workdir at $workdir"
    else
        # Verify source integrity before extraction
        local expected_sha256
        expected_sha256=$(get_package_sha256 "${IGOS_PACKAGES}/${pkg_dir}/package.yml")
        if ! verify_source_checksum "${IGOS_SOURCES}/${tarball}" "$expected_sha256"; then
            log "error: source integrity check failed for ${tarball} — aborting"
            [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_pkg_exit "$name" 1 0
            return 1
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
    fi

    local start=$(date +%s)
    local _pkg_start_ms _phase_start_ms _phase_dur_ms
    _pkg_start_ms=$(date +%s%3N)

    # Apply declared patches BEFORE sourcing build.sh (parity with
    # igos-build.py's styles/base.py:_patch_commands). Helper is sourced
    # from pkg-functions.sh. Skipped for sourceless packages (no source
    # tree to patch into). mitkrb halt 2026-05-10 surfaced this gap.
    if [ -n "$tarball" ]; then
        cd "$workdir"
        _phase_start_ms=$(date +%s%3N)
        if ! apply_package_patches "${IGOS_PACKAGES}/${pkg_dir}/package.yml" >> "$pkg_log" 2>&1; then
            log "  FAILED in patch-apply"
            tail -20 "$pkg_log" | while IFS= read -r l; do log "    $l"; done
            _phase_dur_ms=$(( $(date +%s%3N) - _phase_start_ms ))
            if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
                trace_pkg_phase "$name" patch_apply 1 "$_phase_dur_ms"
                pkg_trace_finish core-extra "$name" "$version" "$pkg_log" "$build_script" "$_pkg_start_ms" 1 patch_apply
            fi
            return 1
        fi
        _phase_dur_ms=$(( $(date +%s%3N) - _phase_start_ms ))
        [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_pkg_phase "$name" patch_apply 0 "$_phase_dur_ms"
    fi

    # Clear any previously-defined functions
    unset -f configure build check do_install post_install

    # Scrub leaked lib32 -m32 env if the previous package died mid-install
    # (marker-keyed, loud when it fires — pkg-functions.sh, W1-a).
    lib32_env_scrub

    # Refresh env from /etc/profile.d/*.sh so packages installed earlier in
    # this phase (rust → /opt/rustc/bin via rustc.sh, etc.) are on PATH.
    source_profile_d

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
        _phase_start_ms=$(date +%s%3N)
        pkg_run_phase configure "$pkg_log"
        local rc=$PKG_PHASE_RC
        _phase_dur_ms=$(( $(date +%s%3N) - _phase_start_ms ))
        [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_pkg_phase "$name" configure "$rc" "$_phase_dur_ms"
        if [ $rc -ne 0 ]; then
            log "  FAILED in configure (exit $rc)"
            tail -20 "$pkg_log" | while IFS= read -r l; do log "    $l"; done
            pkg_trace_finish core-extra "$name" "$version" "$pkg_log" "$build_script" "$_pkg_start_ms" "$rc" configure
            return 1
        fi
        log "  [CONFIGURE] done"
    fi

    # --- BUILD ---
    if declare -f build > /dev/null 2>&1; then
        cd "$workdir"
        log "  [BUILD] starting..."
        _phase_start_ms=$(date +%s%3N)
        pkg_run_phase build "$pkg_log"
        local rc=$PKG_PHASE_RC
        _phase_dur_ms=$(( $(date +%s%3N) - _phase_start_ms ))
        [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_pkg_phase "$name" build "$rc" "$_phase_dur_ms"
        if [ $rc -ne 0 ]; then
            log "  FAILED in build (exit $rc)"
            tail -20 "$pkg_log" | while IFS= read -r l; do log "    $l"; done
            pkg_trace_finish core-extra "$name" "$version" "$pkg_log" "$build_script" "$_pkg_start_ms" "$rc" build
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
        _phase_start_ms=$(date +%s%3N)
        # NON-FATAL: pkg_run_phase captures rc (errexit-protected + exit-safe).
        # A test-suite failure is logged + trace-recorded but does NOT stop the
        # build (consistent with ch8 + LFS treating `make check` as
        # informational; chroot test flakes must not block the ISO).
        pkg_run_phase check "$pkg_log"
        local rc=$PKG_PHASE_RC
        _phase_dur_ms=$(( $(date +%s%3N) - _phase_start_ms ))
        [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_pkg_phase "$name" check "$rc" "$_phase_dur_ms"
        if [ $rc -ne 0 ]; then
            log "  [CHECK] FAILED (exit $rc) — NON-FATAL (test-suite result; build continues; full output in log + trace)"
            tail -20 "$pkg_log" | while IFS= read -r l; do log "    $l"; done
        fi
        log "  [CHECK] done"
    fi

    # --- INSTALL (via DESTDIR staging + package tracking) ---
    cd "$workdir"
    log "  [INSTALL] staging..."
    _phase_start_ms=$(date +%s%3N)
    # Bracketed (NOT `|| rc=$?`): a `||` operand suspends errexit through
    # pkg_install -> pkg_stage -> pkg_run_phase, defeating the do_install
    # subshell protection. set +e captures rc without aborting; set -e restores.
    local rc=0
    # Release honesty — see chroot-build-ch8.sh for the full rationale. Empty
    # release => no PACKAGE RELEASE header => import leaves the recorded release
    # alone, rather than asserting a default as fact.
    local _pkg_release
    _pkg_release=$(get_package_release "${IGOS_PACKAGES}/${pkg_dir}/package.yml")
    set +e
    pkg_install "$name" "$version" "$description" "$_pkg_release"
    rc=$?
    set -e
    _phase_dur_ms=$(( $(date +%s%3N) - _phase_start_ms ))
    [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_pkg_phase "$name" install "$rc" "$_phase_dur_ms"
    if [ $rc -ne 0 ]; then
        log "  FAILED in install/staging (exit $rc)"
        pkg_trace_finish core-extra "$name" "$version" "$pkg_log" "$build_script" "$_pkg_start_ms" "$rc" install
        return 1
    fi

    # --- POST-INSTALL (runs on live system if defined) ---
    if declare -f post_install > /dev/null 2>&1; then
        cd "$workdir"
        log "  [POST-INSTALL] running live system hooks..."
        _phase_start_ms=$(date +%s%3N)
        # Errexit+exit-safe phase runner (mirrors the do_install handling
        # above): a failing post_install — e.g. systemd-sysusers on a missing
        # file — must HALT LOUDLY. Raw `post_install >> log` under set -e would
        # set-e-exit the driver silently (the 2026-06-02 openldap silent halt);
        # ignoring pi_rc would let a broken post_install ship. 2026-06-02 fix.
        # Capture the package's own file hashes BEFORE the hook runs, so a
        # file it rewrites in place is recorded as hook-managed content
        # rather than left looking like damage to every later check.
        pkg_hook_baseline "$name"
        pkg_run_phase post_install "$pkg_log"
        local pi_rc=$PKG_PHASE_RC
        _phase_dur_ms=$(( $(date +%s%3N) - _phase_start_ms ))
        [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_pkg_phase "$name" post_install "$pi_rc" "$_phase_dur_ms"
        if [ "$pi_rc" -ne 0 ]; then
            log "  FAILED in post_install (exit $pi_rc)"
            pkg_trace_finish core-extra "$name" "$version" "$pkg_log" "$build_script" "$_pkg_start_ms" "$pi_rc" post_install
            return 1
        fi
        pkg_record_hook_changes "$name"
        log "  [POST-INSTALL] done"
    fi

    local elapsed=$(( $(date +%s) - start ))
    log "  SUCCESS: ${name} ${version} (${elapsed}s)"
    log ""

    pkg_trace_finish core-extra "$name" "$version" "$pkg_log" "$build_script" "$_pkg_start_ms" 0 all
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
            igos_progress_skip "$name" "resuming from $IGOS_START_AT"
            log "  Skipping: $name (resuming from $IGOS_START_AT)"
            return 0
        fi
    fi

    # Two-statement form (NOT `build_core_package "$@" || { ... }`).
    # FIX-THIS-ASAP 2026-05-24: see chroot-build-ch8.sh's equivalent
    # comment block for the full errexit-suspension class explanation.
    # core-extra was the original surfacing site — apparmor + linux-
    # firmware silent partial-installs both lived here.
    igos_progress_begin "$name"
    build_core_package "$@"
    local bcp_rc=$?
    igos_progress_end "$name" "$bcp_rc"
    if [ "$bcp_rc" -ne 0 ]; then
        log ""
        log "error: build failed: $name"
        log "    Fix the issue and re-run with the orchestrator (both flags required):"
        log "      sudo IGOS_START_AT=$name bash scripts/build-intergenos.sh \\"
        log "          --user \$USER --root-password \$RP --user-password \$UP \\"
        log "          --start-at core-extra --checkpoint --stop-after bootloader"
        log ""
        log "    IGOS_START_AT alone does not short-circuit earlier phases — it is"
        log "    a package-skip honored only within chroot-build-core-extra.sh."
        log "    Without --start-at core-extra, the orchestrator re-runs every"
        log "    phase from validate (and phase_setup's chown will rewrite the"
        log "    populated chroot's ownership)."
        log ""
        exit 1
    fi

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
log ">>> InterGenOS core extra build"
# Count the run_package CALL SITES, not every line starting with the
# word: the bare '^run_package' pattern also matched this script's own
# `run_package() {` definition line, so every tier has been reporting
# one more package than it builds (measured 2026-08-05: this script
# said 91/225/34 where the plans hold 90/224/33). The trailing quote
# is what tells a call from the definition — every call site passes a
# quoted first argument.
EXTRA_PKG_COUNT=$(grep -c '^run_package "' "$0" 2>/dev/null || echo "?")
# The total comes from the plan itself — the run_package call sites in
# this script — so adding a package cannot leave the counter stale. The
# tier name is the ORCHESTRATOR PHASE name, which is what a watcher
# correlates progress against.
igos_progress_init core-extra "$EXTRA_PKG_COUNT"
log "    ${EXTRA_PKG_COUNT} packages beyond LFS 13.0"
log "    start: $(date)"
log "    cores: ${IGOS_JOBS}"
log ""

# Initialize package database (continues from Chapter 8)
pkg_init

# 'which' FIRST — built before every other core-extra package. Some upstream
# build systems probe for tools with `which` (e.g. apparmor's Make.rules does
# `which awk`); without which in the chroot they misdetect and fail. LFS base
# does not ship which, so it must be installed early. Moved here 2026-06-01
# after apparmor silently failed its `which awk` probe (was late in this tier).
run_package "which" "which" "2.23" \
    "which-2.23.tar.gz" \
    "Utility to show the full path of commands"

# --- POSIX capabilities + the util-linux second pass ---
# libcap-ng must precede util-linux-pass2: the pass exists to rebuild
# util-linux with --enable-setpriv, which the LFS-exact pass 1
# (util-linux-core, §8.82) cannot build because libcap-ng is not part
# of LFS. qemu/libvirt/swtpm consume libcap-ng downstream.

run_package "libcap-ng" "libcap-ng" "0.9.3" \
    "libcap-ng-0.9.3.tar.gz" \
    "POSIX capabilities library and utilities"

run_package "util-linux-pass2" "util-linux-pass2" "2.41.3" \
    "util-linux-2.41.3.tar.xz" \
    "util-linux pass 2 with libcap-ng-backed setpriv"

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

run_package "ca-certificates" "ca-certificates" "2026.04.30" \
    "ca-certificates-2026.04.30.tar.gz" \
    "Mozilla CA root certificate bundle (PEM) for OS-level TLS verification"

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

# --- Group F.1: FDE initramfs prerequisites (D-001 LUKS activation chain) ---
# Statically-linked binaries bundled into the FDE initramfs for LUKS unlock.
# Same posture as busybox-static (no chroot runtime deps; can be placed
# anywhere in the build order AFTER openssl since the EXPERIMENTAL pair
# link against system libcrypto.a). All three are tier:core; wiring here
# is REQUIRED — tier:core packages are not topo-sort-built by the Python
# builder; phase_core_extra runs this bash script in hardcoded order.
# Silent-skip pattern (Rulebook Rule 2) if absent from this list.

run_package "cryptsetup-static" "cryptsetup-static" "2.8.4" \
    "cryptsetup-2.8.4.tar.xz" \
    "Statically-linked cryptsetup for early-boot LUKS unlock"

run_package "tpm2-tools-static" "tpm2-tools-static" "5.7" \
    "tpm2-tools-5.7.tar.gz" \
    "Statically-linked tpm2-tools subset for EXPERIMENTAL TPM2 LUKS unlock"

run_package "fido2-tools-static" "fido2-tools-static" "1.17.0" \
    "libfido2-1.17.0.tar.gz" \
    "Statically-linked libfido2 tools for EXPERIMENTAL FIDO2 LUKS unlock"

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

run_package "pkm" "pkm" "0.2.0" \
    "" \
    "InterGenOS package manager — install, remove, query, verify"

run_package "intergenos-keyring" "intergenos-keyring" "0.1.0" \
    "" \
    "InterGenOS GPG release keyring — /etc/pkm/trusted.gpg"

run_package "intergenos-base-files" "intergenos-base-files" "1.0.0" \
    "" \
    "Class 11 canonical owner — /etc baseline + FHS skeleton + systemd-preset + tmpfiles"

run_package "intergenos-legal" "intergenos-legal" "0.1.0" \
    "" \
    "InterGenOS legal documents — LICENSE + SOURCES.md to /usr/share/doc/intergenos/"

run_package "intergenos-helper-lib" "intergenos-helper-lib" "1.0.0" \
    "" \
    "Sourceable bash library for pkm install helpers — /usr/share/igos/helpers/helper-lib.sh (H-007)"

run_package "intergenos-default-settings" "intergenos-default-settings" "1.0.0" \
    "" \
    "InterGenOS canonical GNOME defaults — gschema-overrides + /etc/skel libadwaita bridge + GDM greeter dconf (D-006 SSoT)"

run_package "help2man" "help2man" "1.49.3" \
    "help2man-1.49.3.tar.xz" \
    "Generate man pages from --help output"

run_package "keyutils" "keyutils" "1.6.3" \
    "keyutils-1.6.3.tar.gz" \
    "Linux kernel key management utilities"

# mandoc: BSD man-page formatter. Required by efivar's docs/Makefile to
# convert .mdoc source → traditional man pages at build time. Retiered
# desktop→core (tier reflects intrinsic nature: man-page formatter,
# analogous to groff which is core). Approved 2026-05-10 after
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

# cpio: moved up from Group H so it builds before shim-signed (which
# depends on cpio for rpm2cpio extraction of the Fedora shim package).
# Caught by preflight-build-order (Scan A) on first-ever-fresh-revert
# build run 2026-05-22 — mirrors the lua promotion at line ~557.
run_package "cpio" "cpio" "2.15" \
    "cpio-2.15.tar.bz2" \
    "GNU cpio - copies files into or out of archives"

# T0-2 A-001 (2026-05-18): wire shim-signed per D-002 ratified day-0.
# Fedora-piggyback MS-signed shim ships to /usr/share/shim-signed/; Forge
# copies to ESP at install time. Depends on rpm (rpm2cpio) + cpio.
run_package "shim-signed" "shim-signed" "16.1" \
    "shim-x64-16.1-8.x86_64.rpm" \
    "Microsoft-signed UEFI shim bootloader (Fedora-piggyback per D-002; dual 2011+2023 CA)"

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

run_package "liburing" "liburing" "2.14" \
    "liburing-2.14.tar.gz" \
    "Linux io_uring async I/O wrapper library"

run_package "fuse3" "fuse3" "3.18.1" \
    "fuse-3.18.1.tar.gz" \
    "Filesystem in Userspace"

run_package "go" "go" "1.26.4" \
    "go1.26.4.linux-amd64.tar.gz" \
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

run_package "intel-ucode" "intel-ucode" "20260512" \
    "microcode-20260512.tar.gz" \
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

run_package "linux-firmware" "linux-firmware" "20260622" \
    "linux-firmware-20260622.tar.xz" \
    "Firmware files for Linux kernel drivers (WiFi, GPU, audio, etc.)"

run_package "sof-firmware" "sof-firmware" "2025.12.2" \
    "sof-bin-2025.12.2.tar.gz" \
    "Sound Open Firmware binaries for Intel audio DSPs"

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

run_package "intergenos-firewall-defaults" "intergenos-firewall-defaults" "1.0.0" \
    "" \
    "InterGenOS default-deny nftables policy — /etc/nftables.conf (D-011 SSoT)"

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

run_package "semantic-version" "semantic-version" "2.10.0" \
    "semantic_version-2.10.0.tar.gz" \
    "Semantic-versioning library (setuptools-rust runtime dep)"

run_package "setuptools-rust" "setuptools-rust" "1.10.2" \
    "setuptools_rust-1.10.2.tar.gz" \
    "Setuptools Rust extension plugin"

run_package "maturin" "maturin" "1.13.1" \
    "maturin-1.13.1.tar.gz" \
    "PEP 517 build backend for Rust+Python wheels (for python-cryptography)"

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

# -- Python build backends retiered ai->core (decided 2026-07-21: every build
# backend lives in core with setuptools/hatchling/maturin — the training-stack
# wave authored these three in ai; retier + this wiring restores the class's
# single home). scikit-build-core's runtime deps (packaging, pathspec) build
# earlier in this file.
run_package "pdm-backend" "pdm-backend" "2.4.9" \
    "pdm_backend-2.4.9.tar.gz" \
    "Python build backend (PDM)"

run_package "scikit-build-core" "scikit-build-core" "1.0.3" \
    "scikit_build_core-1.0.3.tar.gz" \
    "Python build backend bridging CMake"

run_package "versioneer" "versioneer" "0.29" \
    "versioneer-0.29.tar.gz" \
    "VCS-based version-string management for Python builds"

run_package "pygments" "pygments" "2.19.2" \
    "pygments-2.19.2.tar.gz" \
    "Syntax highlighting library"

# -- pytest test stack + jq's regex engine (oniguruma) — added 2026-06-23.
# oniguruma is a tier:core C lib (jq, tier:base, links it); iniconfig + pytest
# complete the Python testing stack (pluggy/packaging/pygments already above).
# All three are ISO-resident (iso_include: true), never mirror-only.
run_package "oniguruma" "oniguruma" "6.9.10" \
    "onig-6.9.10.tar.gz" \
    "Multi-charset regular-expression library"

run_package "iniconfig" "iniconfig" "2.3.0" \
    "iniconfig-2.3.0.tar.gz" \
    "Brain-dead simple parsing of ini files"

run_package "pytest" "pytest" "9.1.1" \
    "pytest-9.1.1.tar.gz" \
    "Simple powerful testing framework"

# -- InterGen web UI Python dependencies (B-009, 14 packages) --
# Build order: C extensions first (multidict, frozenlist, propcache),
# then pure Python leaf deps, then packages with deps, then top-level.

# Build backends the stack below needs under offline --no-build-isolation but
# which are NOT otherwise present in the chroot (2026-06-02 pre-emptive scan):
#   poetry-core -> pkgconfig, aiohappyeyeballs, rich (poetry.core.masonry.api)
#   expandvars  -> frozenlist, propcache, yarl (in-tree pep517_backend imports it)
#   pkgconfig   -> aiohttp (probes system llhttp at build time)
# poetry-core first (self-bootstraps, backend-path=src); pkgconfig needs
# poetry-core installed. expandvars uses hatchling (already in the chroot).
run_package "poetry-core" "poetry-core" "2.4.1" \
    "poetry_core-2.4.1.tar.gz" \
    "Poetry PEP 517 build backend"
run_package "expandvars" "expandvars" "1.1.2" \
    "expandvars-1.1.2.tar.gz" \
    "Unix-style variable expansion (build dep)"
run_package "pkgconfig" "pkgconfig" "1.6.0" \
    "pkgconfig-1.6.0.tar.gz" \
    "Python pkg-config wrapper (build dep)"

run_package "multidict" "multidict" "6.7.1" \
    "multidict-6.7.1.tar.gz" \
    "Multi-key dictionary (several values per key)"
run_package "frozenlist" "frozenlist" "1.8.0" \
    "frozenlist-1.8.0.tar.gz" \
    "List-like structure that can be made immutable"
run_package "propcache" "propcache" "0.5.2" \
    "propcache-0.5.2.tar.gz" \
    "Accelerated property cache for Python"
run_package "idna" "idna" "3.16" \
    "idna-3.16.tar.gz" \
    "Internationalized Domain Names in Applications"
run_package "wcwidth" "wcwidth" "0.7.0" \
    "wcwidth-0.7.0.tar.gz" \
    "Measure rendered width of East Asian characters"
run_package "mdurl" "mdurl" "0.1.2" \
    "mdurl-0.1.2.tar.gz" \
    "URL utilities for markdown-it-py"
run_package "typing-extensions" "typing-extensions" "4.15.0" \
    "typing_extensions-4.15.0.tar.gz" \
    "Backported and experimental type hints"
run_package "yarl" "yarl" "1.24.2" \
    "yarl-1.24.2.tar.gz" \
    "Yet another URL library"
run_package "aiohappyeyeballs" "aiohappyeyeballs" "2.6.2" \
    "aiohappyeyeballs-2.6.2.tar.gz" \
    "Happy Eyeballs connection helper for asyncio"
run_package "aiosignal" "aiosignal" "1.4.0" \
    "aiosignal-1.4.0.tar.gz" \
    "List of registered asynchronous callbacks"
run_package "markdown-it-py" "markdown-it-py" "4.2.0" \
    "markdown_it_py-4.2.0.tar.gz" \
    "Markdown parser with 100% CommonMark support"
# attrs is an unconditional RUNTIME dep of aiohttp (its do_install resolves
# runtime deps against the chroot); build it before aiohttp. 2026-06-02
# runtime-dep scan. (async-timeout is gated python_version<3.11, N/A on py3.14.)
run_package "attrs" "attrs" "26.1.0" \
    "attrs-26.1.0.tar.gz" \
    "Classes without boilerplate (aiohttp runtime dep)"
run_package "aiohttp" "aiohttp" "3.13.5" \
    "aiohttp-3.13.5.tar.gz" \
    "Async HTTP client/server framework"
run_package "prompt-toolkit" "prompt-toolkit" "3.0.52" \
    "prompt_toolkit-3.0.52.tar.gz" \
    "Library for building interactive command lines"
run_package "rich" "rich" "15.0.0" \
    "rich-15.0.0.tar.gz" \
    "Rich text and beautiful formatting in the terminal"

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

# --- Group T0-3: Installer runtime dependencies ---
# Authored 2026-05-19 against audit M-002 (chroot-binary-presence gap that hid
# parted/sgdisk/mkfs.xfs/etc. absence) + remediation plan T0-3 sub-cluster 1.
# All nine packages are tier:core; wiring here is REQUIRED per the phantom-
# package class (Rulebook Rule 2 + D-009 item 3) — tier:core packages are not
# topo-sort-built by the Python builder; phase_core_extra runs this bash script
# in hardcoded order. Silent-skip if absent from this list.
#
# Ordering rationale:
#   - inih + liburcu build first (xfsprogs deps, both standalone libs)
#   - mdadm/parted/dialog/ntfs-3g/gptfdisk are leaf-or-near-leaf (deps already
#     present from earlier groups: lvm2 G+H, popt earlier, ncurses-core earlier)
#   - xfsprogs depends on inih+liburcu (declared above)
#   - os-prober is last (runtime-deps on ntfs-3g + util-linux-core, both built
#     by now)

run_package "inih" "inih" "62" \
    "inih-r62.tar.gz" \
    "Simple INI file parser (xfsprogs + exiv2 dep)"

run_package "liburcu" "liburcu" "0.15.3" \
    "userspace-rcu-0.15.3.tar.bz2" \
    "Userspace RCU (read-copy-update) library — xfsprogs dep"

run_package "mdadm" "mdadm" "4.4" \
    "mdadm-4.4.tar.xz" \
    "Linux MD software RAID administration utility"

run_package "parted" "parted" "3.7" \
    "parted-3.7.tar.xz" \
    "GNU Parted disk partition manipulation (parted, partprobe, libparted)"

run_package "ntfs-3g" "ntfs-3g" "2026.2.25" \
    "ntfs-3g_ntfsprogs-2026.2.25.tgz" \
    "NTFS driver + ntfsprogs utilities (ntfsresize, ntfsfix, mkntfs) — installer NTFS probe dep"

run_package "exfatprogs" "exfatprogs" "1.4.2" \
    "exfatprogs-1.4.2.tar.xz" \
    "exFAT filesystem utilities (mkfs.exfat, fsck.exfat) — external-drive exFAT support"

run_package "gptfdisk" "gptfdisk" "1.0.10" \
    "gptfdisk-1.0.10.tar.gz" \
    "GPT fdisk partition tools (sgdisk, cgdisk, gdisk, fixparts) — installer GPT op dep"

run_package "xfsprogs" "xfsprogs" "7.0.0" \
    "xfsprogs-7.0.0.tar.xz" \
    "XFS filesystem utilities (mkfs.xfs, xfs_repair, xfs_admin, etc.)"

run_package "os-prober" "os-prober" "1.84" \
    "os-prober-1.84.tar.gz" \
    "Detect other OSes for grub dual-boot menu generation (grub-mkconfig dep)"

# Rust-built systemd generator (rust is built far earlier at the rust/cargo-c
# cluster above). Provides RAM-backed compressed swap on installed systems —
# the security-correct alternative to a plaintext-leaking disk swapfile.
run_package "zram-generator" "zram-generator" "1.1.2" \
    "zram-generator-1.1.2.tar.gz" \
    "Systemd generator for zram compressed swap (RAM-only; no plaintext-to-disk leak)"

# --- 2026-06-23: NK1 smartcard / PIV signing stack — STRICT DEP ORDER.
# pcsc-lite (daemon+lib) → ccid (USB reader driver, deps pcsc-lite) →
# opensc (PKCS#11 module, deps pcsc-lite; PATCHED for NK1 RSA-4096 PIV) →
# libp11 (OpenSSL PKCS#11 engine/provider, deps openssl). This stack lets an
# InterGenOS host drive the Nitrokey NK1 signing ceremonies natively. All
# transitive deps (libusb/systemd/polkit/openssl/zlib/readline + build-time
# meson/ninja/pkgconf/flex/perl-core) are built earlier in core.

run_package "pcsc-lite" "pcsc-lite" "2.5.1" \
    "pcsc-lite-2.5.1.tar.xz" \
    "PC/SC smart card daemon and middleware library"

run_package "ccid" "ccid" "1.8.2" \
    "ccid-1.8.2.tar.xz" \
    "PC/SC CCID/ICCD USB smart card reader driver for pcscd"

run_package "opensc" "opensc" "0.27.1" \
    "opensc-0.27.1.tar.gz" \
    "PKCS#11 modules and tools for smart cards (PIV) — NK1 RSA-4096 patched"

run_package "libp11" "libp11" "0.4.18" \
    "libp11-0.4.18.tar.gz" \
    "PKCS#11 wrapper library and OpenSSL engine/provider for smart cards"

# ============================================================================
# gyp (gyp-next) — 64-bit host build tool; MUST stay ABOVE the lib32 tail
# (no 64-bit package may follow a lib32 one in this shell). Forced dep of
# lib32-nss's build.sh gyp path (decided 2026-07-02).
run_package "gyp" "gyp" "0.22.2" \
    "gyp_next-0.22.2.tar.gz" \
    "Generate Your Projects - meta-build system (gyp-next)"

# lib32 core substrate (GE arc, Wave 1) — LAST in this driver by design:
# the lib32-env.sh profile exports -m32 CC/CXX and cleans up after itself
# (lib32_env_end), but tail position additionally guarantees no 64-bit
# package ever follows a lib32 one in this shell (build-rules §2.2
# env-leak class — belt AND suspenders). Mirror-only; lib32-glibc builds
# earlier, in ch8 right after glibc-core.
# ============================================================================

run_package "lib32-zlib" "lib32-zlib" "1.3.2" \
    "zlib-1.3.2.tar.gz" \
    "Compression library (32-bit multilib runtime)"

run_package "lib32-zstd" "lib32-zstd" "1.5.7" \
    "zstd-1.5.7.tar.gz" \
    "Zstandard real-time compression (32-bit multilib runtime)"

run_package "lib32-expat" "lib32-expat" "2.7.4" \
    "expat-2.7.4.tar.xz" \
    "XML parser library (32-bit multilib runtime)"

run_package "lib32-libffi" "lib32-libffi" "3.5.2" \
    "libffi-3.5.2.tar.gz" \
    "Foreign Function Interface library (32-bit multilib runtime)"

run_package "lib32-libgpg-error" "lib32-libgpg-error" "1.59" \
    "libgpg-error-1.59.tar.bz2" \
    "GPG error code library (32-bit multilib runtime)"

# lib32-elfutils (GE-01 L18): libelf.so.1 for lib32-mesa's radeonsi
# (parses LLVM-emitted ELF shader objects at runtime). Needs lib32-zlib
# + lib32-zstd above.
run_package "lib32-elfutils" "lib32-elfutils" "0.194" \
    "elfutils-0.194.tar.bz2" \
    "ELF object file access library, libelf only (32-bit multilib runtime)"

# lib32-libxcrypt BEFORE lib32-systemd-libs: systemd's meson configure
# hard-requires libcrypt (launch-7 halt 2026-07-03 — the wave-2 authoring
# missed this twin; build-time demand, not a runtime NEEDED).
run_package "lib32-libxcrypt" "lib32-libxcrypt" "4.5.2" \
    "libxcrypt-4.5.2.tar.xz" \
    "Modern password hashing library (32-bit multilib runtime)"

run_package "lib32-systemd-libs" "lib32-systemd-libs" "259.1" \
    "systemd-259.1.tar.gz" \
    "systemd shared libraries - libudev + libsystemd (32-bit multilib runtime)"

# lib32-dbus AFTER lib32-systemd-libs: the 32-bit libdbus-1.so.3 links
# libsystemd (readelf-verified on the 64-bit sibling), and the -Dsystemd=enabled
# meson probe resolves the 32-bit libsystemd.pc that lib32-systemd-libs installs.
# The floor for gamemode's full 32-bit client half (libgamemode links dbus-1);
# GE-tooling wave 2026-07-09.
run_package "lib32-dbus" "lib32-dbus" "1.16.2" \
    "dbus-1.16.2.tar.xz" \
    "D-Bus client library - libdbus-1 (32-bit multilib runtime)"

# The nss forced closure (decided 2026-07-02), in dependency
# order: nspr/sqlite/libtasn1 (glibc-only) -> p11-kit (libtasn1+libffi)
# -> nss last (nspr+sqlite+zlib at link, p11-kit's trust module at
# runtime; gyp+ninja drive its build).
run_package "lib32-nspr" "lib32-nspr" "4.38.2" \
    "nspr-4.38.2.tar.gz" \
    "Netscape Portable Runtime (32-bit multilib runtime)"

run_package "lib32-sqlite" "lib32-sqlite" "3510200" \
    "sqlite-autoconf-3510200.tar.gz" \
    "SQL database engine (32-bit multilib runtime)"

run_package "lib32-libtasn1" "lib32-libtasn1" "4.21.0" \
    "libtasn1-4.21.0.tar.gz" \
    "ASN.1 library (32-bit multilib runtime)"

run_package "lib32-p11-kit" "lib32-p11-kit" "0.26.2" \
    "p11-kit-0.26.2.tar.xz" \
    "PKCS11 module loading library (32-bit multilib runtime)"

run_package "lib32-nss" "lib32-nss" "3.121" \
    "nss-3.121.tar.gz" \
    "Network Security Services (32-bit multilib runtime)"

# lib32-llvm needs lib32-{zlib,zstd,libffi} above (LLVM links all three).
# It carries its own toolchain-file-driven cmake build; the lib32-env
# profile is sourced by its build.sh for the staging helpers.
run_package "lib32-llvm" "lib32-llvm" "21.1.8" \
    "llvm-21.1.8.src.tar.xz" \
    "LLVM compiler infrastructure (32-bit runtime + build interface)"

# Compute-tier prerequisites (mirror-only ROCm stack builds against these):
# libnuma is rocr-runtime's runtime dep; msgpack is Tensile's fast
# logic-file path at rocblas build time.
run_package "numactl" "numactl" "2.0.19" \
    "numactl-2.0.19.tar.gz" \
    "NUMA policy control library (libnuma) and utilities"

run_package "python-msgpack" "python-msgpack" "1.2.1" \
    "msgpack-1.2.1.tar.gz" \
    "MessagePack serializer for Python (Tensile logic-file fast path)"

run_package "python-joblib" "python-joblib" "1.5.3" \
    "joblib-1.5.3.tar.gz" \
    "Lightweight pipelining/parallelism for Python (Tensile build parallelism)"

run_package "msgpack-cxx" "msgpack-cxx" "8.0.0" \
    "msgpack-cxx-8.0.0.tar.gz" \
    "MessagePack C++ headers (rocblas/Tensile msgpack logic-file reader)"

run_package "python-ply" "python-ply" "3.11" \
    "ply-3.11.tar.gz" \
    "Python Lex-Yacc (CppHeaderParser's lexer backend)"

run_package "python-cppheaderparser" "python-cppheaderparser" "2.7.4" \
    "CppHeaderParser-2.7.4.tar.gz" \
    "C++ header parser for Python (hipamd code-generation build dep)"

# soupsieve before beautifulsoup4: beautifulsoup4 declares it as a required
# runtime dependency and imports it from bs4/css.py. Both are mirror-only
# (iso_include: false) — their consumer, scripts/parse-blfs-book.py, runs on
# the build host and is never present on an installed system.
run_package "soupsieve" "soupsieve" "2.9.1" \
    "soupsieve-2.9.1.tar.gz" \
    "CSS selector library for Python (beautifulsoup4's selector engine)"

run_package "beautifulsoup4" "beautifulsoup4" "4.15.0" \
    "beautifulsoup4-4.15.0.tar.gz" \
    "HTML/XML parsing library for Python (the BLFS book parser)"

# python-pip: source-less (the wheel ships inside the interpreter's own
# ensurepip bundle); owns the pip payload that previously reached the
# chroot only as an interpreter self-install (A-3, decided 2026-07-28).
run_package "python-pip" "python-pip" "25.3" \
    "" \
    "Python package installer (ensurepip-bootstrapped, owned payload)"

# ============================================================================
# Summary
# ============================================================================

TOTAL_CORE_EXTRA=$(grep -c "^run_package" "$0" | head -1)
TOTAL_TRACKED=$(ls /var/lib/igos/packages/ 2>/dev/null | wc -l)

log ""
log ">>> Core extra build complete"
log "    Total tracked packages: ${TOTAL_TRACKED}"
log "    end: $(date)"
