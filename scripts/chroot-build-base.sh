#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
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

# Source the shared build-output library (timestamp prefix, one phase/step
# style, error:/warning:/note: severity, TTY-aware color). This tier's log()
# below keeps its own sinks (tee to the tier text log, mirror to the trace).
# shellcheck source=lib/logging.sh
[ -f /mnt/intergenos/scripts/lib/logging.sh ] && source /mnt/intergenos/scripts/lib/logging.sh

# Source the forensic-trace bash companion (no-op when verbose unset).
# shellcheck disable=SC1091
[ -f /mnt/intergenos/scripts/lib/trace.sh ] && source /mnt/intergenos/scripts/lib/trace.sh
if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
    trace_init "tier-base"
    _BASE_TIER_START_MS=$(date +%s%3N)
    trace_event tier_start tier=base log_file="$IGOS_LOGS/base-build.log"
    _base_trace_exit() {
        local rc=$?
        local end_ms duration_ms
        end_ms=$(date +%s%3N)
        duration_ms=$((end_ms - _BASE_TIER_START_MS))
        trace_event tier_end tier=base rc::=$rc duration_ms::=$duration_ms
        trace_close
        return $rc
    }
    trap _base_trace_exit EXIT
fi

# ============================================================================
# Logging
# ============================================================================

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
    echo "[$(igos_timestamp)] $*" | tee -a "$IGOS_LOGS/base-build.log" ${IGOS_BUILD_STREAM:+"$IGOS_BUILD_STREAM"}
    if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
        trace_event tier_narration tier=base text="$*"
    fi
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
    # Export the package template dir so pkg_stage's overlay_files() can deploy
    # this package's files/ tree into DESTDIR (2026-06-02 bash-driver files/
    # overlay fix). Re-set per package — no stale leak across iterations.
    export PKG_TEMPLATE_DIR="${IGOS_PACKAGES}/${pkg_dir}"
    local pkg_log="${IGOS_LOGS}/${name}-base-$(date '+%Y%m%d-%H%M%S').log"
    local workdir="/tmp/igos-build/${name}"

    if [ ! -f "$build_script" ]; then
        log "error: no build.sh found at $build_script"
        return 1
    fi

    log ">>> Base: ${name} ${version}"
    log "    log: ${pkg_log}"

    export PKG_VERSION="$version"

    # Pin every subsequent event in this function to this package via the
    # forensic-trace boundary. trace_pkg_enter emits the pkg_enter event;
    # trace_pkg_exit is emitted at every return site below.
    if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
        trace_pkg_enter "$name" "$version" "base"
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
    local _pkg_start_ms
    _pkg_start_ms=$(date +%s%3N)

    # Apply declared patches BEFORE sourcing build.sh (parity with
    # igos-build.py's styles/base.py:_patch_commands). Helper is sourced
    # from pkg-functions.sh. mitkrb halt 2026-05-10 surfaced this gap;
    # rsync's security_fix patch was also un-applied in prior builds.
    cd "$workdir"
    local _phase_start_ms _phase_dur_ms
    _phase_start_ms=$(date +%s%3N)
    if ! apply_package_patches "${IGOS_PACKAGES}/${pkg_dir}/package.yml" >> "$pkg_log" 2>&1; then
        log "  FAILED in patch-apply"
        tail -20 "$pkg_log" | while IFS= read -r l; do log "    $l"; done
        _phase_dur_ms=$(( $(date +%s%3N) - _phase_start_ms ))
        if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
            trace_pkg_phase "$name" patch_apply 1 "$_phase_dur_ms"
            pkg_trace_finish base "$name" "$version" "$pkg_log" "$build_script" "$_pkg_start_ms" 1 patch_apply
        fi
        return 1
    fi
    _phase_dur_ms=$(( $(date +%s%3N) - _phase_start_ms ))
    [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_pkg_phase "$name" patch_apply 0 "$_phase_dur_ms"

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
        _phase_start_ms=$(date +%s%3N)
        pkg_run_phase configure "$pkg_log"
        local rc=$PKG_PHASE_RC
        _phase_dur_ms=$(( $(date +%s%3N) - _phase_start_ms ))
        [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_pkg_phase "$name" configure "$rc" "$_phase_dur_ms"
        if [ $rc -ne 0 ]; then
            log "  FAILED in configure (exit $rc)"
            tail -20 "$pkg_log" | while IFS= read -r l; do log "    $l"; done
            pkg_trace_finish base "$name" "$version" "$pkg_log" "$build_script" "$_pkg_start_ms" "$rc" configure
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
            pkg_trace_finish base "$name" "$version" "$pkg_log" "$build_script" "$_pkg_start_ms" "$rc" build
            return 1
        fi
        log "  [BUILD] done"
    fi

    # --- CHECK ---
    # Check policy (comment reconciled 2026-07-03 — the old "Tests-as-truth:
    # any check() failure halts the tier build" header contradicted the
    # NON-FATAL implementation 12 lines below it): test suites RUN on every
    # package and their results are logged + trace-recorded, but a failure
    # is INFORMATIONAL — it does not stop the tier (LFS-consistent). The
    # `tests:` block in package.yml (see docs/test-allow-list.md) exists to
    # TAG expected environmental failures auditably, not to gate the build.
    # Bare `|| true` in check() is still forbidden — failures must reach
    # the log and the trace, never vanish.
    if declare -f check > /dev/null 2>&1; then
        cd "$workdir"
        log "  [CHECK] starting..."
        _phase_start_ms=$(date +%s%3N)
        # NON-FATAL: pkg_run_phase captures rc (errexit-protected + exit-safe).
        # A test-suite failure is logged + trace-recorded but does NOT stop the
        # build (consistent with ch8 + LFS treating `make check` as
        # informational; chroot test flakes like gnulib test-execute.sh must
        # not block the ISO).
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

    # --- INSTALL ---
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
        pkg_trace_finish base "$name" "$version" "$pkg_log" "$build_script" "$_pkg_start_ms" "$rc" install
        return 1
    fi

    # --- POST-INSTALL ---
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
            pkg_trace_finish base "$name" "$version" "$pkg_log" "$build_script" "$_pkg_start_ms" "$pi_rc" post_install
            return 1
        fi
        pkg_record_hook_changes "$name"
        log "  [POST-INSTALL] done"
    fi

    local elapsed=$(( $(date +%s) - start ))
    log "  SUCCESS: ${name} ${version} (${elapsed}s)"
    log ""

    pkg_trace_finish base "$name" "$version" "$pkg_log" "$build_script" "$_pkg_start_ms" 0 all
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
    local version="$3"

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

    # Skip if already tracked. Must be exact <name>-<version> match.
    # Prior `compgen -G "${name}-*"` greedy-globbed: `at-*` silently matched
    # `at-spi2-core-2.58.3` (built earlier in desktop tier), causing the
    # base/at package to be skipped entirely with no error. Same shape
    # affects any short-prefix package whose name is a prefix of another
    # tracked package's name. Exact match eliminates the entire class.
    if [ -d "/var/lib/igos/packages/${name}-${version}" ]; then
        igos_progress_skip "$name" "already tracked"
        log "  Skipping: $name (already tracked at /var/lib/igos/packages/${name}-${version})"
        return 0
    fi

    # Two-statement form (NOT `build_base_package "$@" || { ... }`).
    # FIX-THIS-ASAP 2026-05-24: see chroot-build-ch8.sh's equivalent
    # comment block for the full errexit-suspension class explanation.
    igos_progress_begin "$name"
    build_base_package "$@"
    local bbp_rc=$?
    igos_progress_end "$name" "$bbp_rc"
    if [ "$bbp_rc" -ne 0 ]; then
        log ""
        log "error: build failed: $name"
        log "    Fix the issue and re-run with the orchestrator (both flags required):"
        log "      sudo IGOS_START_AT=$name bash scripts/build-intergenos.sh \\"
        log "          --user \$USER --root-password \$RP --user-password \$UP \\"
        log "          --start-at base --checkpoint --stop-after bootloader"
        log ""
        log "    IGOS_START_AT alone does not short-circuit earlier phases — it is"
        log "    a package-skip honored only within chroot-build-base.sh. The"
        log "    --start-at base flag is required to skip all phases before base."
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
# Build Order — base packages
#
# Dependencies that are in core (already installed) don't need to be
# listed here. Only inter-base dependencies affect ordering.
# ============================================================================

log ""
log ">>> InterGenOS base package build"
# Count the run_package CALL SITES, not every line starting with the
# word: the bare '^run_package' pattern also matched this script's own
# `run_package() {` definition line, so every tier has been reporting
# one more package than it builds (measured 2026-08-05: this script
# said 91/225/34 where the plans hold 90/224/33). The trailing quote
# is what tells a call from the definition — every call site passes a
# quoted first argument.
BASE_PKG_COUNT=$(grep -c '^run_package "' "$0" 2>/dev/null || echo "?")
# The total comes from the plan itself — the run_package call sites in
# this script — so adding a package cannot leave the counter stale. The
# tier name is the ORCHESTRATOR PHASE name, which is what a watcher
# correlates progress against.
igos_progress_init base "$BASE_PKG_COUNT"
log "    ${BASE_PKG_COUNT} packages"
log "    start: $(date)"
log "    cores: ${IGOS_JOBS}"
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

run_package "unzip" "unzip" "6.0" \
    "unzip60.tar.gz" \
    "Extractor for PKZIP-compatible .zip archives"

# --- 2026-05-22 evening CST: arrivals from extra (Rust-static CLI tools)
# and core (dialog) retiers per BASE_CLI canonical-rule in
# validate-package-tiers.py. These are QoL CLI utilities a tier:base
# system end-user expects. dialog also moved off chroot-build-core-extra.sh
# in the same commit. Build before phase_desktop / phase_extra. ---

run_package "bat" "bat" "0.26.1" \
    "bat-0.26.1.tar.gz" \
    "cat(1) clone with syntax highlighting + git integration (Rust)"

run_package "dialog" "dialog" "1.3-20260107" \
    "dialog-1.3-20260107.tgz" \
    "TUI dialog-box widget library + binary (libdialog + dialog) — installer TUI dep"

run_package "fd" "fd" "9.0.0" \
    "fd-9.0.0.tar.gz" \
    "Simple, fast, user-friendly find(1) alternative (Rust)"

run_package "ripgrep" "ripgrep" "14.1.0" \
    "ripgrep-14.1.0.tar.gz" \
    "Line-oriented recursive regex search tool (Rust)"

# jq — command-line JSON processor (links tier:core oniguruma). Added 2026-06-23.
run_package "jq" "jq" "1.8.2" \
    "jq-1.8.2.tar.gz" \
    "Command-line JSON processor"

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

# --- 2026-06-23: host-migration build-artifact + dev tooling ---
# squashfs-tools/xorriso/mtools = the ISO/image-construction toolchain (lets an
# InterGenOS box build InterGenOS ISOs); sshpass + neovim = dev/admin tooling.
# All deps (zlib/xz/zstd/lz4/lzo/readline core; cmake/ninja/gettext/gperf for
# neovim's bundled-deps build) are core-tier, built before base. tmux is NOT
# here — it links libevent (desktop tier) so it lives in extra (build order).

run_package "squashfs-tools" "squashfs-tools" "4.7.5" \
    "squashfs-tools-4.7.5.tar.gz" \
    "Tools to create and extract Squashfs filesystems"

run_package "xorriso" "xorriso" "1.5.8.pl02" \
    "xorriso-1.5.8.pl02.tar.gz" \
    "ISO 9660 / Rock Ridge filesystem image manipulation"

run_package "mtools" "mtools" "4.0.49" \
    "mtools-4.0.49.tar.gz" \
    "Access FAT (MS-DOS) filesystems without mounting"

run_package "sshpass" "sshpass" "1.10" \
    "sshpass-1.10.tar.gz" \
    "Non-interactive ssh password provider"

run_package "neovim" "neovim" "0.12.3" \
    "neovim-0.12.3.tar.gz" \
    "Hyperextensible Vim-based text editor"

# Network client utilities (BLFS) — DNS / whois / traceroute tools users expect.
run_package "bind-utils" "bind-utils" "9.20.19" \
    "bind-9.20.19.tar.xz" \
    "BIND DNS client utilities (dig, host, nslookup)"

run_package "whois" "whois" "5.6.6" \
    "whois_5.6.6.tar.xz" \
    "Intelligent WHOIS client"

run_package "traceroute" "traceroute" "2.1.6" \
    "traceroute-2.1.6.tar.gz" \
    "Trace the network path packets take to a host"

# ============================================================================
# Summary
# ============================================================================

TOTAL_TRACKED=$(ls /var/lib/igos/packages/ 2>/dev/null | wc -l)

log ""
log ">>> Base package build complete"
log "    Total tracked packages: ${TOTAL_TRACKED}"
log "    end: $(date)"
