#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# InterGenOS Package Functions — DESTDIR Staging + Slackware-style Tracking
set -e
#
# Sourced by the Chapter 8 build runner. Provides functions to:
#   1. Stage a package's installed files via DESTDIR
#   2. Generate a file manifest
#   3. Create a compressed archive (.igos.tar.gz)
#   4. Deploy staged files to the live filesystem
#   5. Run post-install hooks on the live system
#
# Database: /var/lib/igos/packages/<name>-<version>  (one text file per package)
# Archives: /var/lib/igos/archives/<name>-<version>.igos.tar.gz
#
# Design: Slackware-style manifests — human-readable, cat-inspectable,
# no binary database, no dependency resolution at install time.
# The build system handles build order; this layer tracks installed state.

# ============================================================================
# Configuration
# ============================================================================

IGOS_PKG_DB="/var/lib/igos/packages"
IGOS_PKG_ARCHIVES="/var/lib/igos/archives"
IGOS_PKG_STAGING="/tmp/igos-staging"

# The build's single-flight assertion for pkm lives in its own file so the
# config phases can source it too. pkg-functions.sh sets errexit at its top, and
# a phase script that sourced this whole library to reach one function would
# inherit that — a behaviour change nobody asked for.
for _sf_lib in "$(dirname "${BASH_SOURCE[0]}")/lib/pkm-single-flight.sh" \
               /mnt/intergenos/scripts/lib/pkm-single-flight.sh; do
    if [ -f "$_sf_lib" ]; then . "$_sf_lib"; break; fi
done
unset _sf_lib

# Source the forensic-trace bash companion if available. Idempotent re-source
# is safe (the file guards via IGOS_TRACE_LIB_LOADED). pkg-functions.sh is
# sourced by every chroot-build-*.sh that does per-package builds, so the
# trace functions become available WITHOUT each caller re-sourcing. Safe when
# verbose unset — all trace_* calls become no-ops.
# shellcheck disable=SC1091
if [ -z "${IGOS_TRACE_LIB_LOADED:-}" ] && [ -f /mnt/intergenos/scripts/lib/trace.sh ]; then
    source /mnt/intergenos/scripts/lib/trace.sh
fi

# Source the shared build-output library so the ✓/✗/⚠ markers, TTY-aware color
# vars, and the severity voice are available wherever pkg-functions is sourced.
# Idempotent (the library guards via IGOS_LOGGING_LIB_LOADED). pkg_log/pkg_error
# below keep their own [pkg] prefix + tee-to-log sinks.
# shellcheck source=lib/logging.sh
[ -f /mnt/intergenos/scripts/lib/logging.sh ] && source /mnt/intergenos/scripts/lib/logging.sh

# ============================================================================
# Environment refresh — /etc/profile.d/*.sh
# ============================================================================

# Source every /etc/profile.d/*.sh in the chroot. Login shells do this via
# /etc/profile; the build pipeline runs commands as non-interactive bash and
# would otherwise miss PATH augmentations from BLFS-style installs that put
# their binaries under /opt/<tool>/bin and rely on profile.d to expose them.
#
# Originally surfaced by Build #9 resume #8 cargo-c halt (cargo: command not
# found at exit 127): rust installed cargo to /opt/rustc-1.95.0/bin and wrote
# /etc/profile.d/rustc.sh to extend PATH, but cargo-c's build.sh — running in
# a fresh non-interactive subshell — never saw the PATH extension. Same gap
# would bite future tools (java, go, other /opt/<x>/bin installs).
#
# Call this from each phase script's run_package right before sourcing the
# package's build.sh. Idempotent; safe to call repeatedly.
source_profile_d() {
    if [ -d /etc/profile.d ]; then
        local f
        for f in /etc/profile.d/*.sh; do
            [ -f "$f" ] && . "$f"
        done
    fi
}

# ============================================================================
# lib32 env-leak scrub (GE arc; Wave-1 adversarial-verify finding W1-a)
# ============================================================================

# The bash drivers run every package in ONE shared shell. A lib32 recipe
# sources scripts/lib32-env.sh, which exports -m32 CC/CXX + a pinned
# PKG_CONFIG_LIBDIR and sets the IGOS_LIB32_ENV_ACTIVE marker. The recipe's
# trailing lib32_env_end covers the success path — but a do_install that
# ABORTS under errexit skips it, leaving the -m32 exports live. Call this
# from each driver's package-build function right before sourcing the next
# build.sh: it is marker-keyed (a no-op unless a lib32 profile is actually
# active), loud when it fires (a scrub here means the PREVIOUS package died
# mid-install — that is diagnostic signal, never silence), and makes the
# cleanup guarantee hold regardless of wiring position or how the previous
# package exited. The RT-1 archive-time width audit remains the backstop.
lib32_env_scrub() {
    if [ -n "${IGOS_LIB32_ENV_ACTIVE:-}" ]; then
        echo "WARN: lib32 build env was left active by a prior package (it likely failed mid-install) — scrubbing CC/CXX/PKG_CONFIG_LIBDIR before this package" >&2
        unset CC CXX PKG_CONFIG_LIBDIR LIB32_HOST IGOS_LIB32_ENV_ACTIVE
        # Restore MAKEFLAGS the same way lib32_env_end would have (the
        # profile's RT-8 visibility override must not leak either).
        if [ "${IGOS_LIB32_PREV_MAKEFLAGS-}" = "__unset__" ]; then
            unset MAKEFLAGS
        elif [ -n "${IGOS_LIB32_PREV_MAKEFLAGS-}" ]; then
            MAKEFLAGS="${IGOS_LIB32_PREV_MAKEFLAGS}"; export MAKEFLAGS
        fi
        unset IGOS_LIB32_PREV_MAKEFLAGS
    fi
}

# ============================================================================
# Source integrity verification
# ============================================================================

# Verify SHA256 checksum of a file before extraction.
# Usage: verify_source_checksum <filepath> <expected_sha256>
# Returns 0 on match, 1 on mismatch / missing / malformed checksum.
#
# Strict-type+length: expected MUST be exactly 64 lowercase hex characters.
# Empty / "NEEDS_CHECKSUM" placeholder / wrong-length / non-hex all FAIL.
# This matches the same-shape fix DeepSeek applied for pkm H1 in
# pkm/repo.py:_verify_checksum at master 2eee235. Audit finding S3 in
# docs/research/code_audits/scripts_audit_2026-04-29_spoc.md.
verify_source_checksum() {
    local file="$1"
    local expected="$2"

    if [[ ! "$expected" =~ ^[a-f0-9]{64}$ ]]; then
        echo "[pkg] error: $(basename "$file") has no valid sha256 checksum"
        echo "[pkg]   got: '${expected:0:32}...'"
        echo "[pkg]   expected: 64 lowercase hex chars (sha256 hex digest)"
        [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_event pkg_source_verify file="$(basename "$file")" expected="${expected:0:32}" verified=false reason=malformed_expected
        return 1
    fi

    local actual
    actual=$(sha256sum "$file" | cut -d' ' -f1)

    if [ "$actual" != "$expected" ]; then
        echo "[pkg] error: Checksum mismatch for $(basename "$file")"
        echo "[pkg]   expected: $expected"
        echo "[pkg]   actual:   $actual"
        [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_event pkg_source_verify file="$(basename "$file")" expected="$expected" actual="$actual" verified=false
        return 1
    fi

    echo "[pkg] Checksum verified: $(basename "$file")"
    [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_event pkg_source_verify file="$(basename "$file")" expected="$expected" actual="$actual" verified=true
    return 0
}

# Read SHA256 from a package.yml file.
# Usage: get_package_sha256 <package_yml_path>
# Outputs the sha256 value to stdout.
get_package_sha256() {
    local yml="$1"
    grep 'sha256:' "$yml" 2>/dev/null | head -1 | awk '{print $2}' | tr -d '"' | tr -d "'"
}

# Read the `release:` field from a package.yml, for the PACKAGE RELEASE manifest
# header (release honesty). Same shape as get_package_sha256 above, and the same
# idiom chroot-build-ch10.sh already uses to read the kernel release for
# CONFIG_LOCALVERSION — one way to read a recipe field, not three.
#
# Anchored at col 0 so a nested `release:` inside a comment or a sub-mapping
# cannot win, and digit-validated so a malformed value yields EMPTY rather than
# garbage: pkg_manifest omits the header entirely on empty, which parses as
# "release unstated" and leaves the DB row's recorded release untouched. That is
# the fail-safe direction — a wrong release asserted as fact is worse than one
# not asserted, and pkm's import carries the row's existing value forward.
get_package_release() {
    local yml="$1"
    local rel
    rel=$(grep -m1 '^release:' "$yml" 2>/dev/null | awk '{print $2}' | tr -d '"' | tr -d "'")
    case "$rel" in
        ''|*[!0-9]*) printf '' ;;
        *) printf '%s' "$rel" ;;
    esac
}

# Extract a source archive into $workdir, dispatching by file extension.
# Mirrors the dispatch in igos-build/builder.py for shell-side parity:
#   .zip   -> bsdtar -xf (no --strip-components; many upstream zips ship flat
#             without a top-level dir, e.g., docbook-xml-4.5.zip)
#   .lz    -> bsdtar -xf with --strip-components=1 (lzip — tar lacks native
#             support in the chroot's tar binary)
#   .rpm   -> rpm2cpio | cpio -id (no --strip-components; RPM cpio payload
#             is rooted at the install prefix, e.g. boot/efi/EFI/fedora/)
#   .tar*  -> tar -xf with --strip-components=1 + --no-same-owner/perms
#
# Args:
#   $1 — tarball basename (e.g. docbook-xml-4.5.zip)
#   $2 — workdir to extract into (must exist + be empty)
# Returns: 0 on success, propagates extractor's exit code on failure.
#
# Centralizes what was previously duplicated across chroot-build-ch8.sh,
# chroot-build-base.sh, chroot-build-core-extra.sh, chroot-build-ch10.sh.
# Build #9 halted at docbook-xml when the hardcoded `tar -xf` couldn't
# read the .zip; this helper closes that bug class.
# 2026-05-23 18:14 CDT: added .rpm case after shim-signed exit-1
# ("tar: This does not look like a tar archive"). RPM is a cpio
# archive wrapped in RPM headers; rpm2cpio strips the headers and
# emits the cpio stream. shim-signed is the only .rpm consumer today.
extract_source() {
    local tarball="$1"
    local workdir="$2"
    local src="${IGOS_SOURCES}/${tarball}"

    [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_event pkg_source_extract tarball="$tarball" dst="$workdir" src="$src"

    local _start_ms
    _start_ms=$(date +%s%3N)
    local _rc=0
    case "$tarball" in
        *.zip)
            bsdtar -xf "$src" -C "$workdir" || _rc=$?
            ;;
        *.lz)
            bsdtar -xf "$src" -C "$workdir" --strip-components=1 \
                --no-same-owner --no-same-permissions || _rc=$?
            ;;
        *.rpm)
            # Pipefail-safe: rpm2cpio failure propagates through the pipe.
            (set -o pipefail; rpm2cpio "$src" | (cd "$workdir" && cpio -id --quiet)) || _rc=$?
            ;;
        *.pcf.gz|*.bdf.gz)
            # Single gzipped font file (e.g. unifont .pcf.gz) — NOT a tar archive,
            # so the default `tar -xf` would fail. Decompress into the build dir
            # under its de-gzipped name for the recipe to consume.
            gunzip -c "$src" > "${workdir}/$(basename "$tarball" .gz)" || _rc=$?
            ;;
        *)
            tar -xf "$src" -C "$workdir" --strip-components=1 \
                --no-same-owner --no-same-permissions || _rc=$?
            ;;
    esac

    if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
        local _dur=$(( $(date +%s%3N) - _start_ms ))
        trace_event pkg_source_extract_done tarball="$tarball" dst="$workdir" rc::=$_rc duration_ms::=$_dur
    fi
    return $_rc
}

# Apply patches declared in package.yml's `patches:` block to the current
# working directory (assumed to be the extracted source tree, cd'd into
# before this is called).
#
# Mirrors igos-build/styles/base.py:_patch_commands() so tier:core and
# tier:base packages get the same patch-application behavior as
# tier:desktop/extra/ai packages built via igos-build.py.
#
# For each declared patch:
#   1. Verifies the file exists at $IGOS_PATCHES (which inside the chroot
#      resolves to /sources where both $SOURCES and $PATCHES were copied
#      during phase_setup).
#   2. Verifies SHA256 against the declared value (defense-in-depth alongside
#      phase_verify_sources).
#   3. Applies via `patch -Np1 -i ...` (or zcat/bzcat/xzcat for compressed).
#
# Usage: apply_package_patches <package_yml_path>
# Returns 0 if all declared patches applied cleanly, or if no patches declared.
# Returns 1 on any patch file missing, sha mismatch, or patch-apply failure.
#
# Background: tier:core / tier:base run_package helpers (build_ch8_package,
# build_core_package, build_base_package) historically did not apply patches.
# `# Patch applied by builder PATCH phase (package.yml)` comments in some
# build.sh files documented the intent but the wiring never landed. mitkrb
# halt 2026-05-10 surfaced the gap when mitkrb retiered desktop→core,
# moving from the igos-build.py path (auto-patch) to the run_package path
# (no auto-patch). This helper closes that gap for ALL tier:core+base
# packages.
apply_package_patches() {
    local yml="$1"
    if [ ! -f "$yml" ]; then
        # No package.yml means no declared patches. Don't fail; the build.sh
        # may be the source of truth for sourceless packages (e.g., pkm).
        return 0
    fi

    # Extract patches block via stdlib-only parser. The previous inline
    # `import yaml` approach broke at Ch 8 entry (Build #8 halt 2026-05-11)
    # because chroot Python has no PyYAML — it's itself a Ch 8 package.
    # Schema is ours; a targeted parser is right-sized.
    local patches_list
    patches_list=$(python3 "${SCRIPTS:-/mnt/intergenos/scripts}/parse-package-yml-patches.py" "$yml")
    local py_rc=$?
    if [ $py_rc -ne 0 ]; then
        echo "[pkg] error: apply_package_patches could not parse $yml"
        return 1
    fi

    if [ -z "$patches_list" ]; then
        # No patches declared — common case, success.
        return 0
    fi

    # Apply each patch in declared order. Halt on first failure.
    local pfile psha patch_path actual
    while IFS='|' read -r pfile psha; do
        [ -z "$pfile" ] && continue
        patch_path="${IGOS_PATCHES}/${pfile}"
        if [ ! -f "$patch_path" ]; then
            echo "[pkg] error: patch file not found: ${patch_path}"
            return 1
        fi
        if [ -n "$psha" ]; then
            if [[ ! "$psha" =~ ^[a-f0-9]{64}$ ]]; then
                echo "[pkg] error: patch sha256 malformed for ${pfile}: '${psha:0:32}...'"
                return 1
            fi
            actual=$(sha256sum "$patch_path" | cut -d' ' -f1)
            if [ "$actual" != "$psha" ]; then
                echo "[pkg] error: patch sha mismatch for ${pfile}"
                echo "[pkg]   expected: $psha"
                echo "[pkg]   actual:   $actual"
                return 1
            fi
        fi
        echo "[pkg] Applying patch: ${pfile}"
        [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_event pkg_patch_apply patch="$pfile" sha="$psha"
        case "$pfile" in
            *.gz)
                if ! zcat "$patch_path" | patch -Np1; then
                    echo "[pkg] error: patch -Np1 (gz) failed for ${pfile}"
                    return 1
                fi
                ;;
            *.bz2)
                if ! bzcat "$patch_path" | patch -Np1; then
                    echo "[pkg] error: patch -Np1 (bz2) failed for ${pfile}"
                    return 1
                fi
                ;;
            *.xz)
                if ! xzcat "$patch_path" | patch -Np1; then
                    echo "[pkg] error: patch -Np1 (xz) failed for ${pfile}"
                    return 1
                fi
                ;;
            *)
                if ! patch -Np1 -i "$patch_path"; then
                    echo "[pkg] error: patch -Np1 failed for ${pfile}"
                    return 1
                fi
                ;;
        esac
    done <<< "$patches_list"

    return 0
}

# ============================================================================
# Internal helpers
# ============================================================================

pkg_log() {
    echo "[pkg] $*" | tee -a "$IGOS_LOGS/pkg-install.log"
}

pkg_error() {
    echo "[pkg] error: $*" | tee -a "$IGOS_LOGS/pkg-install.log" >&2
}

# ============================================================================
# pkg_trace_finish — flush the FULL per-package byte capture (per-pkg log +
# the build script) into the forensic trace, then emit pkg_exit.
#
# Usage: pkg_trace_finish <tier> <name> <version> <pkg_log> <build_script> \
#                         <start_ms> <rc> <phase>
#
# MUST be called on EVERY exit path of a per-package build — success AND
# failure. Before 2026-06-01 the drivers ran trace_pkg_capture only on the
# success path; with `set -e` active the driver aborted on the first failing
# phase, so a failed build's bytes never reached the trace OR the general log —
# they survived only in the per-package log. That defeated the every-byte
# trace mandate exactly when it mattered (see the vim-9.2.0078 install halt).
# Centralising the flush here lets every driver call it from each failure path.
#
# No-op (returns 0) when the trace framework is not loaded.
# ============================================================================
pkg_trace_finish() {
    [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] || return 0
    local tier="$1" name="$2" version="$3" pkg_log="$4" build_script="$5"
    local start_ms="$6" rc="$7" phase="$8"
    local dur=$(( $(date +%s%3N) - start_ms ))
    trace_pkg_capture --pkg "$name" --version "$version" --tier "$tier" \
        --phase "$phase" --rc "$rc" --duration-ms "$dur" \
        --log "$pkg_log" --cmd-file "$build_script"
    trace_pkg_exit "$name" "$rc" "$dur"
}

# ============================================================================
# pkg_run_phase — run a build.sh phase function with FULL failure protection.
#
# Usage:  pkg_run_phase <phase_func> <log_file>
#         # then read $PKG_PHASE_RC  (0 = success, non-zero = failure)
#
# Three guarantees, each EMPIRICALLY VERIFIED 2026-06-01 (a prior "two-statement
# form keeps errexit" fix was never validated and did NOT hold — the suspension
# propagates through function calls AND nested subshells):
#
#   1. errexit ACTIVE inside the phase. `( set -e; "$func" )` runs the phase in
#      a subshell with errexit on, so an unmasked mid-phase command failure
#      ABORTS the phase instead of continuing to a false success. This is what
#      stops SILENT PARTIAL INSTALLS (apparmor/keyutils/openssh sailed through
#      on 2026-06-01 because the old `func || rc=$?` SUSPENDED errexit).
#
#   2. `exit N` inside the phase is CONTAINED. Many build.sh assertions use a
#      bare `exit 1` (linux-firmware, grub, shim-signed, ~34 files). Run inline,
#      that `exit` terminates the whole driver process, bypassing all failure
#      capture (the linux-firmware-20260309 silent halt). In the subshell it
#      only ends the subshell.
#
#   3. rc is captured into the global $PKG_PHASE_RC and this helper ALWAYS
#      returns 0, so the CALLER must invoke it BARE (no `|| rc=$?`). Calling any
#      wrapper as a `||`/`&&` operand re-suspends errexit through the whole
#      dynamic extent — defeating guarantee 1. Callers read $PKG_PHASE_RC.
#
# `set +e` brackets the subshell so it is a standalone statement (not a `||`
# operand) and this function's own set -e does not abort before rc is read.
# The caller's PRIOR errexit state is SAVED and RESTORED (not forced on): forcing
# `set -e` leaks the flag globally and can abort a caller that is mid `set +e`
# bracket (e.g. a driver running `set +e; pkg_install; rc=$?; set -e`). errexit
# (`set -e`) is global shell state, not function-scoped — verified 2026-06-01.
#
# CALL CONTRACT (critical): invoke pkg_run_phase BARE — never as a `||`/`&&`/`if`
# operand. The bash errexit-suspension of a `||` operand propagates through every
# nested function call AND subshell in its dynamic extent, which would re-suspend
# the `( set -e; "$func" )` errexit and silently swallow failures. The same rule
# applies to pkg_install (do_install runs through it): drivers must call it as
# `set +e; pkg_install ...; rc=$?; set -e`, NOT `pkg_install ... || rc=$?`.
# Output (stdout+stderr) is APPENDED to <log_file>.
# ============================================================================
PKG_PHASE_RC=0
pkg_run_phase() {
    local func="$1" log="$2" _e
    case $- in *e*) _e=1 ;; *) _e=0 ;; esac
    set +e
    ( set -e; "$func" ) >> "$log" 2>&1
    PKG_PHASE_RC=$?
    if [ "$_e" = 1 ]; then set -e; else set +e; fi
    return 0
}

# ============================================================================
# pkg_init — Create database and archive directories
# ============================================================================

pkg_init() {
    mkdir -pv "$IGOS_PKG_DB"
    mkdir -pv "$IGOS_PKG_ARCHIVES"
    mkdir -pv "$IGOS_PKG_STAGING"
}

# ============================================================================
# bundle_license — Populate DESTDIR/usr/share/licenses/<pkg_name>/ (K21.B)
#
# Usage: bundle_license <pkg_name> <src_dir> <destdir>
#
# Pure-bash mirror of the Python-tier hook igos-build/builder.py:bundle_license
# (which delegates to igos-build/license_bundle.py). This path serves the bash
# tiers (core / base / core-extra / ch10) and CANNOT import that module —
# it runs inside the chroot, where python doesn't exist until mid-ch8. The two
# runtimes implement identical strategy semantics; the K21.B gate
# (scripts/check-license-bundle.sh at phase_squashfs) is the cross-check that
# fails the build loudly if EITHER leaves a package without a bundle.
#
# Strategies, applied in order — first to populate the dir wins:
#   S1 upstream-extract : LICENSE/LICENCE/COPYING/COPYRIGHT/NOTICE (+ .ext /
#                         -variant) found via -maxdepth 2 (top-level + immediate
#                         subdirs: licenses/, license-files/, doc/, docs/).
#   S2 pass-variant     : *-pass1/-pass2/-pam/-static → mirror the BASE
#                         package's already-installed license dir.
#   S3 first-party      : intergen-/intergenos-/pkm/igos-build/forge/*-helper →
#                         ship the project GPL-3.0-or-later LICENSE (from the
#                         in-chroot-reachable intergenos-legal copy; §3.5).
#   S4 spdx-stub        : a declared SPDX license but no extractable text →
#                         write a LICENSE-BY-SPDX attribution stub.
#
# Closes the gap (S1-only here, S2/3/4 only in the post-hoc backfill tool) that
# left ~71 packages unbundled on a clean build and tripped K21.B (2026-06-03).
#
# Skip-if-already-staged: if the dir already has content (do_install staged
# licenses explicitly), this hook is a no-op — don't clobber.
#
# Never fails the build. If no strategy matches, returns 0; the K21.B gate is
# the hard check and SPDX in package.yml is the canonical license-of-record.
# ============================================================================

# Small YAML scalar reader for the S4 stub. The bash hook runs INSIDE the
# chroot, where python may not yet exist (python is built mid-ch8), so we
# cannot shell out to a parser — read the top-level scalar with pure bash.
_bl_yaml() {
    # $1 = top-level key, $2 = package.yml path. Echoes the de-quoted value.
    local v
    v=$(grep -E "^$1:[[:space:]]" "$2" 2>/dev/null | head -1 || true)
    [ -z "$v" ] && return 0
    v="${v#*:}"                          # drop "key:"
    v="${v#"${v%%[![:space:]]*}"}"       # ltrim
    v="${v%"${v##*[![:space:]]}"}"       # rtrim
    v="${v#\"}"; v="${v%\"}"             # strip surrounding double quotes
    v="${v#\'}"; v="${v%\'}"             # strip surrounding single quotes
    printf '%s' "$v"
}

bundle_license() {
    local pkg_name="$1"
    local src_dir="${2:-$PWD}"
    local destdir="${3:-$DESTDIR}"

    # Minimal safety — never fail the build.
    [ -z "$pkg_name" ] && return 0
    [ -z "$destdir" ] && return 0

    local license_dir="${destdir}/usr/share/licenses/${pkg_name}"

    # Skip if build.sh's do_install already staged licenses explicitly.
    # Match the Python builder's same-name check semantics (don't clobber).
    if [ -d "$license_dir" ] && [ -n "$(ls -A "$license_dir" 2>/dev/null)" ]; then
        return 0
    fi

    # ---- S1: upstream license files in the source tree -------------------
    # -maxdepth 2 catches top-level files PLUS files in immediate subdirs
    # (licenses/, doc/, etc.). Mirrors license_bundle.find_license_files.
    if [ -d "$src_dir" ]; then
        local found
        found=$(find "$src_dir" -maxdepth 2 -type f \
            \( -iname 'LICENSE'    -o -iname 'LICENSE.*'   -o -iname 'LICENSE-*' \
               -o -iname 'LICENSE_*' \
               -o -iname 'LICENCE'  -o -iname 'LICENCE.*'  -o -iname 'LICENCE-*' \
               -o -iname 'COPYING'  -o -iname 'COPYING.*'  -o -iname 'COPYING-*' \
               -o -iname 'COPYRIGHT' -o -iname 'COPYRIGHT.*' \
               -o -iname 'NOTICE'   -o -iname 'NOTICE.*' \) \
            2>/dev/null || true)
        if [ -n "$found" ]; then
            mkdir -p "$license_dir"
            local count=0
            while IFS= read -r f; do
                [ -z "$f" ] && continue
                # Flat layout under license_dir (basename only); K21.B only
                # requires the dir be non-empty.
                cp -p "$f" "${license_dir}/$(basename "$f")" 2>/dev/null \
                    && count=$((count + 1)) || true
            done <<< "$found"
            if [ "$count" -gt 0 ]; then
                # cp -p preserves source-tree OWNERSHIP too, and packages
                # whose test step chowns the source tree to the build test
                # user (the LFS `chown -R tester .` pattern) then seal that
                # user into the archive metadata (measured: 5 license files
                # across sed/make/gawk/findutils on ge9b-12 archives).
                # Staged payload is root's.
                chown -R 0:0 "$license_dir" 2>/dev/null || true
                pkg_log "bundle_license: S1 staged ${count} upstream file(s) for ${pkg_name}"
                return 0
            fi
            rmdir "$license_dir" 2>/dev/null || true
        fi
    fi

    # ---- S2: pass-variant — mirror the base package's installed licenses --
    # *-pass1/-pass2/-pam/-static whose own source carried no license file:
    # copy from the BASE package, installed earlier into the live chroot root.
    local base="" suffix
    for suffix in -pass1 -pass2 -pam -static; do
        case "$pkg_name" in
            *"$suffix") base="${pkg_name%"$suffix"}"; break ;;
        esac
    done
    if [ -n "$base" ]; then
        local base_dir="/usr/share/licenses/${base}"   # REAL chroot root
        if [ -d "$base_dir" ] && [ -n "$(ls -A "$base_dir" 2>/dev/null)" ]; then
            mkdir -p "$license_dir"
            if cp -a "${base_dir}/." "${license_dir}/" 2>/dev/null \
               && [ -n "$(ls -A "$license_dir" 2>/dev/null)" ]; then
                # Same ownership normalization as S1 — cp -a preserves owners.
                chown -R 0:0 "$license_dir" 2>/dev/null || true
                pkg_log "bundle_license: S2 mirrored ${base} licenses for ${pkg_name}"
                return 0
            fi
            rmdir "$license_dir" 2>/dev/null || true
        fi
    fi

    # ---- S3: first-party InterGenOS package — ship project GPL-3.0 -------
    # Repo-root /mnt/intergenos/LICENSE is NOT synced into the chroot
    # (build-rules §3.5), so use the byte-identical copy shipped by
    # intergenos-legal, which IS reachable via the packages/ rsync. (The
    # Python hook, running host-side, uses the canonical repo-root LICENSE.)
    case "$pkg_name" in
        intergen-*|intergenos-*|pkm|igos-build|forge|*-helper)
            local fp="/mnt/intergenos/packages/core/intergenos-legal/LICENSE"
            if [ -f "$fp" ]; then
                mkdir -p "$license_dir"
                if cp -p "$fp" "${license_dir}/LICENSE" 2>/dev/null; then
                    pkg_log "bundle_license: S3 first-party GPL-3.0-or-later for ${pkg_name}"
                    return 0
                fi
                rmdir "$license_dir" 2>/dev/null || true
            fi
            ;;
    esac

    # ---- S4: SPDX-only stub from package.yml's declaration ---------------
    # The SPDX identifier is the legally-binding declaration; the full text
    # just isn't bundled. Matches license_bundle.spdx_stub_text byte-for-byte.
    if [ -n "${PKG_TEMPLATE_DIR:-}" ] && [ -f "${PKG_TEMPLATE_DIR}/package.yml" ]; then
        local yml="${PKG_TEMPLATE_DIR}/package.yml"
        local spdx version tier
        spdx=$(_bl_yaml license "$yml")
        [ -z "$spdx" ] && spdx=$(_bl_yaml payload_license "$yml")
        if [ -n "$spdx" ]; then
            version=$(_bl_yaml version "$yml")
            tier=$(_bl_yaml tier "$yml")
            [ -z "$tier" ] && tier="unknown"
            mkdir -p "$license_dir"
            cat > "${license_dir}/LICENSE-BY-SPDX" <<EOF
${pkg_name} ${version}

This package is licensed under: ${spdx}

SPDX identifier is the canonical license-of-record. The
upstream source tree did not include an explicit
LICENSE/COPYING/COPYRIGHT/NOTICE file at a standard path,
so this stub serves as the per-package license attribution
per InterGenOS K21.B compliance gate.

For the full license text, refer to the canonical SPDX
reference at https://spdx.org/licenses/${spdx}.html (or the
applicable subentry for compound SPDX expressions).

Source-of-record: packages/${tier}/${pkg_name}/package.yml
EOF
            pkg_log "bundle_license: S4 SPDX-stub (${spdx}) for ${pkg_name}"
            return 0
        fi
    fi

    # No strategy matched — warning condition, NOT a build-fail. The K21.B
    # gate (scripts/check-license-bundle.sh at phase_squashfs) is the hard
    # check; SPDX in package.yml is the canonical license-of-record.
    return 0
}


# ============================================================================
# overlay_files — deploy a package's files/ template tree into DESTDIR
#
# Usage: overlay_files <pkg_template_dir> [destdir]
#
# Bash-builder mirror of igos-build/builder.py:overlay_files() (the Python-
# builder equivalent). Closes the gap surfaced 2026-06-02: the files/-overlay
# (added 2026-05-27 / commit fa28e435 for the sysusers.d migration) was wired
# ONLY into the Python builder, so packages built via chroot-build-{ch8,base,
# core-extra,ch10}.sh had their files/ trees SILENTLY not deployed. That broke
# openldap (post_install ran systemd-sysusers on a /usr/lib/sysusers.d/
# openldap.conf that never landed) and would have broken at/exim/fcron too.
#
# Semantics: cp -an (archive + no-clobber) so anything do_install already wrote
# WINS; the overlay only ADDS missing paths — matches the Python builder and
# bundle_license's skip-if-already-staged philosophy. intergenos-base-files
# (which installs its own files/ explicitly via cp -av in do_install) is
# therefore unaffected — its versions are already present, so cp -an skips them.
#
# Returns 0 for benign no-ops (no files/ dir). A genuine cp failure returns
# non-zero so the caller (pkg_stage) can HALT LOUDLY rather than ship a package
# missing its overlay files.
# ============================================================================

overlay_files() {
    local pkg_template_dir="$1"
    local destdir="${2:-$DESTDIR}"

    [ -z "$pkg_template_dir" ] && return 0
    [ -z "$destdir" ] && return 0
    local files_dir="${pkg_template_dir}/files"
    [ -d "$files_dir" ] || return 0

    # cp -an: -a archive (preserve modes/symlinks/timestamps), -n no-clobber
    # (do_install's explicit writes win). Mirror of builder.py overlay_files.
    if cp -an "${files_dir}/." "${destdir}/"; then
        # Normalize ownership of every files/-sourced path to root:root. cp -a
        # (= --preserve=all) above copied the REPO source's owner — the build
        # user's uid/gid (e.g. 1000) — into DESTDIR; that uid maps to the live
        # `intergenos` user and would ship /etc/passwd + /etc/group (0664)
        # WRITABLE and /etc/shadow readable by an unprivileged user = local root
        # escalation (found 2026-06-04). Repo-source ownership is never
        # meaningful for the target; service-user ownership is set by explicit
        # post_install chowns on build-output paths, not files/ overlay paths.
        # Chown by path so this also covers do_install self-deployers.
        ( cd "${files_dir}" && find . -depth -print0 ) | while IFS= read -r -d '' rel; do
            chown -h root:root "${destdir}/${rel#./}" 2>/dev/null || true
        done
        local n
        n=$(find "$files_dir" -type f 2>/dev/null | wc -l)
        if [ "${n:-0}" -gt 0 ]; then
            pkg_log "overlay-files: deployed ${n} file(s) from files/ for $(basename "$pkg_template_dir")"
        fi
        return 0
    fi

    pkg_error "overlay-files: cp -an from ${files_dir} to ${destdir} FAILED"
    return 1
}


# ============================================================================
# pkg_stage — Run install() with DESTDIR pointing to a staging directory
#
# Usage: pkg_stage <name> <version>
#
# Expects:
#   - CWD is the package build directory
#   - An install() function is defined (from the package's build.sh)
#   - Or a pkg_custom_install() function for exception packages
#
# Sets: PKG_DEST (the staging root for this package)
# ============================================================================

pkg_stage() {
    local name="$1"
    local version="$2"

    export PKG_DEST="${IGOS_PKG_STAGING}/${name}-${version}"

    # Clean any prior staging attempt
    rm -rf "$PKG_DEST"
    mkdir -pv "$PKG_DEST"

    # Mirror root-level symlinks so DESTDIR installs follow them.
    # Without this, `make install DESTDIR=...` creates real /lib, /bin, /sbin
    # directories that collide with the root filesystem's symlinks.
    for link in bin lib sbin; do
        if [ -L "/$link" ]; then
            ln -sv "usr/$link" "${PKG_DEST}/$link"
        fi
    done
    case $(uname -m) in
        x86_64) mkdir -pv "${PKG_DEST}/lib64" ;;
    esac
    mkdir -pv "${PKG_DEST}/usr/"{bin,lib,sbin}

    # Capture the source-tree workdir BEFORE do_install runs so
    # bundle_license has a stable reference even if do_install cd's
    # into a build/ subdir without restoring. Callers (chroot-build-
    # {ch8,base,core-extra}.sh's build_*_package) cd to $workdir
    # before invoking pkg_install/pkg_stage, so $PWD here is the
    # extracted source tree.
    local src_dir_for_license="$PWD"

    # Export DESTDIR for autotools/meson packages
    export DESTDIR="$PKG_DEST"

    pkg_log "Staging ${name}-${version} to ${PKG_DEST}"

    # Run the package's do_install function
    # Named do_install (not install) to avoid collision with /usr/bin/install.
    # Output appends to the most recent build log for this package so all
    # output is in one place. Falls back to a standalone install log.
    local install_log
    install_log=$(ls -t "${IGOS_LOGS}/${name}-"*".log" 2>/dev/null | head -1)
    if [ -z "$install_log" ]; then
        install_log="${IGOS_LOGS}/${name}-install-$(date '+%Y%m%d-%H%M%S').log"
    fi

    local rc=0
    if declare -f do_install > /dev/null 2>&1; then
        echo "=== [INSTALL] $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$install_log"
        # Shared errexit+exit-safe phase runner: catches unmasked mid-install
        # failures (no silent partial install) AND contains a bare `exit N` in
        # the package's do_install. See pkg_run_phase for the full rationale.
        pkg_run_phase do_install "$install_log"
        rc=$PKG_PHASE_RC
    else
        pkg_error "No do_install() function defined for ${name}"
        return 1
    fi

    if [ $rc -ne 0 ]; then
        pkg_error "Staging failed for ${name}-${version} (exit $rc)"
        # Surface the failure to the GENERAL build log (this function's stdout),
        # not only the per-package install log — a failed install must never be
        # silent. The full bytes remain in $install_log and are flushed to the
        # trace by the driver's failure path via pkg_trace_finish.
        echo "[pkg] ---- last 30 lines of $(basename "$install_log") ----"
        tail -n 30 "$install_log" 2>/dev/null
        echo "[pkg] ---- end install-failure excerpt ----"
        return 1
    fi

    # Verify something was actually staged
    local file_count
    file_count=$(find "$PKG_DEST" -not -type d | wc -l)
    if [ "$file_count" -eq 0 ]; then
        pkg_error "Staging produced no files for ${name}-${version}"
        pkg_error "Check that do_install() uses \$DESTDIR or the correct staging variable"
        return 1
    fi

    pkg_log "Staged ${file_count} files for ${name}-${version}"

    # 2026-06-02: deploy the package's files/ template tree into DESTDIR before
    # pkg_manifest captures the staging tree. Bash-builder mirror of igos-build/
    # builder.py:overlay_files() — the Python builder had this since 2026-05-27
    # (fa28e435) but the 4 bash drivers never did, so bash-tier packages' files/
    # trees silently never deployed (openldap/at/exim/fcron sysusers breakage).
    # cp -an no-clobber so do_install's explicit writes win. PKG_TEMPLATE_DIR is
    # exported per-package by chroot-build-{ch8,base,core-extra,ch10}.sh. A real
    # cp failure HALTS staging loudly rather than shipping an incomplete package.
    if [ -n "${PKG_TEMPLATE_DIR:-}" ]; then
        overlay_files "$PKG_TEMPLATE_DIR" "$PKG_DEST"
        local overlay_rc=$?
        if [ "$overlay_rc" -ne 0 ]; then
            pkg_error "files/ overlay failed for ${name}-${version}"
            unset DESTDIR
            return 1
        fi
    fi

    # K21.B 2026-05-24: bundle upstream license files into DESTDIR before
    # pkg_manifest captures the staging tree. Mirror of igos-build/builder
    # .py:bundle_license() for the bash-builder path. Closes the 315-
    # package K21.B audit gap surfaced 2026-05-24 (every package built
    # via chroot-build-{ch8,base,core-extra}.sh shipped without
    # /usr/share/licenses/<pkg>/; only Python-builder packages had it).
    # No-op when build.sh's do_install already staged licenses
    # explicitly (skip-if-populated check inside the function).
    bundle_license "$name" "$src_dir_for_license" "$PKG_DEST"

    # Normalize BUILD-USER-LEAKED files (uid/gid >= 1000) to root:root before
    # manifest/archive/deploy. The build runs as root in the chroot, but
    # cp -a/-p/tar in do_install and bundle_license PRESERVE the repo/source
    # build-user uid (>=1000 — the virtiofs repo + host-generated asset
    # tarballs) into DESTDIR, which then ships into BOTH the package archive
    # (-> installed systems) AND the deployed chroot (-> squashfs).
    # (approved staging-chokepoint fix, 2026-06-25, root:root arc.)
    #
    # L29 (2026-07-05) — TWO defects in the original blanket `chown -h -R`:
    #  1. The kernel CLEARS setuid/setgid on any chown of a regular file,
    #     even by root, even when ownership does not change. The blanket
    #     chown stripped the bits from EVERY setuid binary in the corpus
    #     (sudo/su/passwd/pkexec/...) — the GE-01 corpus shipped with no
    #     working privilege escalation, live AND installed.
    #  2. "No package stages non-root ownership in DESTDIR" was an
    #     unverified claim: at (atd:atd), fcron (fcron:fcron), dbus
    #     (root:messagebus), util-linux wall (root:tty) all stage
    #     legitimate system-user/group ownership, which the blanket chown
    #     flattened.
    # Fix: chown ONLY files whose uid OR gid is >= 1000 (the actual leak
    # class — system users live below 1000), and capture-and-restore
    # suid/sgid modes across the chown. Verified by the setuid-inventory
    # gate at squashfs time (check-setuid-inventory.py), which fail-closes
    # on stripped bits, flattened ownership, and UNEXPECTED setuid files.
    while IFS=' ' read -r _mode _uid _gid _path; do
        [ -n "$_path" ] || continue
        if [ "$_uid" -ge 1000 ] || [ "$_gid" -ge 1000 ]; then
            chown -h root:root "$_path"
            # chown cleared suid/sgid if set — restore the captured mode
            # (symlinks carry no mode; -h chown on them changes owner only)
            if [ -f "$_path" ] && [ $(( 0$_mode & 06000 )) -ne 0 ]; then
                chmod "$_mode" "$_path"
            fi
        fi
    done < <(find "$PKG_DEST" -mindepth 1 \( -type f -o -type d -o -type l \) -printf '%#m %U %G %p\n')

    # L27 durable class fix: drop this function's own pre-seeded FHS-skeleton
    # members from the staging tree when the install left them in seed state,
    # so manifest/archive/deploy downstream never claim the skeleton (GE-01:
    # 908/913 archives carried ./bin ./lib ./sbin ./lib64 — and iso-prep
    # evicting ONE mirror-only package deleted the chroot's merged-usr compat
    # symlinks). Runs AFTER the ownership normalization above so the tree the
    # manifest and archive capture is final.
    pkg_prune_seeded_skeleton "$name" "$PKG_DEST"

    # Unset DESTDIR so it doesn't leak into post-install steps
    unset DESTDIR

    return 0
}

# ============================================================================
# pkg_prune_seeded_skeleton — drop seed-state FHS-skeleton members pre-capture
#
# Usage: pkg_prune_seeded_skeleton <name> <staging-dir>
#
# pkg_stage pre-seeds every DESTDIR with the merged-usr compat symlinks
# (bin/lib/sbin -> usr/*), lib64, and usr/{bin,lib,sbin} so `make install`
# follows the live filesystem's layout — load-bearing DURING the install,
# wrong to CAPTURE afterward: left in place, every archive + manifest claims
# an FHS skeleton the package does not own, which is exactly how L27 bit
# (evicting one package's manifest rows deleted the chroot's compat
# symlinks). The pkm remover's single-segment refusal is the chokepoint
# belt; THIS is the durable class fix.
#
# Removal is seed-state-verified, never blanket (verify, don't assume): a
# compat symlink goes only if it still points exactly at
# usr/<name>; a directory goes only via rmdir, i.e. only when EMPTY —
# glibc's populated /lib64 and any real usr/* content are never touched.
# intergenos-base-files is the canonical FHS-skeleton owner (build-rules
# §2.7) and is exempt: its archive is the ONE that ships the skeleton on
# installed systems.
# ============================================================================

pkg_prune_seeded_skeleton() {
    local name="$1"
    local dest="$2"

    if [ "$name" = "intergenos-base-files" ]; then
        pkg_log "Skeleton prune: skipped for ${name} (canonical FHS-skeleton owner)"
        return 0
    fi

    local link
    for link in bin lib sbin lib64; do
        if [ -L "${dest}/${link}" ] \
           && [ "$(readlink "${dest}/${link}")" = "usr/${link}" ]; then
            rm -f "${dest}/${link}"
        fi
    done

    # rmdir only removes EMPTY dirs by definition; order leaves usr last so
    # a fully-seed-state usr/ tree collapses, while any real content keeps
    # its whole path. Explicit if-blocks, not && chains — a failing final
    # command in a && list trips `set -e` (the errexit-suspension class).
    local d
    for d in lib64 usr/bin usr/lib usr/sbin usr; do
        if [ -d "${dest}/${d}" ] && [ ! -L "${dest}/${d}" ]; then
            rmdir "${dest}/${d}" 2>/dev/null || true
        fi
    done
    return 0
}

# ============================================================================
# pkg_manifest — Generate a Slackware-style manifest from staged files
#
# Usage: pkg_manifest <name> <version> [description]
#
# Writes: $IGOS_PKG_DB/<name>-<version>
# ============================================================================

pkg_manifest() {
    local name="$1"
    local version="$2"
    local description="${3:-No description}"
    # Component A (release honesty): optional 4th arg. When a caller supplies the
    # package release, emit a `PACKAGE RELEASE:` header so `pkm import` can write
    # installed.release truthfully instead of the schema default. All four build
    # drivers pass it — chroot-build-ch8.sh, -base.sh, -ch10.sh and
    # -core-extra.sh each derive it from the recipe's package.yml and thread it
    # through pkg_install. An absent release still emits no header and parses
    # under the legacy-tolerant rule, which is what keeps manifests already in
    # the field readable. The content fingerprint below (BUILD DATE varies per
    # rebuild, so any real rebuild rewrites the manifest bytes) is what pkm keys
    # the re-register on; this header is the separate release-truthfulness rider.
    local release="${4:-}"
    local dest="${IGOS_PKG_STAGING}/${name}-${version}"
    local manifest="${IGOS_PKG_DB}/${name}-${version}"

    if [ ! -d "$dest" ]; then
        pkg_error "No staging directory found for ${name}-${version}"
        return 1
    fi

    # Calculate sizes
    local uncompressed_size
    uncompressed_size=$(du -sb "$dest" | cut -f1)
    local uncompressed_human
    uncompressed_human=$(du -sh "$dest" | cut -f1)

    # Generate file list — paths relative to staging root, sorted, in the
    # canonical manifest shape shared with the Python builder:
    #
    #   "<path>/"                  a directory
    #   "<path> sha256:<64 hex>"   a regular file
    #   "<path>"                   anything else (symlinks, devices)
    #
    # Both annotations were missing here, and both are load-bearing downstream.
    # pkm derives files.is_dir purely from the trailing slash, so without it
    # every directory of every bash-tier package registered as a FILE — the
    # condition pkm/remover.py names when it chooses disk truth over the flag
    # ("the ge9b-08 chroot DB: thousands of real directories carried
    # is_dir=0"), and the reason verify reports an absent directory as a
    # missing file. And with no per-file sha256, `pkm import` had no recorded
    # reference to register against and fell through to hashing whatever was
    # on disk at import time, so the bash tier had no independent content
    # record at all.
    #
    # The read loop rather than a pipeline of cut/awk is deliberate: manifest
    # paths may contain spaces (linux-firmware ships several), the sha suffix
    # is anchored at end-of-line precisely so those paths survive, and a
    # whitespace-splitting producer would defeat the parser that exists to
    # handle them. A file whose hash cannot be read emits its bare path — the
    # legacy shape, which every consumer still accepts — rather than a wrong
    # or empty annotation.
    local file_list
    file_list=$(
        cd "$dest" && find . -mindepth 1 -printf '%y\t%P\n' \
            | sort -t"$(printf '\t')" -k2 \
            | while IFS="$(printf '\t')" read -r _type _path; do
                case "$_type" in
                    d)
                        printf '%s/\n' "$_path"
                        ;;
                    f)
                        # Hash from standard input, never by name: given a
                        # file name containing a backslash, sha256sum writes
                        # its output line in escaped form (a leading "\"
                        # before the digest), and the field cut below then
                        # carried that backslash into the manifest —
                        # systemd's three system-systemd\x2d*.slice units
                        # verified as "missing" on every R001.2 install.
                        _hash=$(sha256sum < "$_path" 2>/dev/null | cut -d' ' -f1)
                        if [ -n "$_hash" ]; then
                            printf '%s sha256:%s\n' "$_path" "$_hash"
                        else
                            printf '%s\n' "$_path"
                        fi
                        ;;
                    *)
                        printf '%s\n' "$_path"
                        ;;
                esac
            done
    )

    # Optional PACKAGE RELEASE header (Component A release honesty). Built as a
    # standalone line so an unset release emits NOTHING (no blank "PACKAGE
    # RELEASE:" that a parser might mis-read as release 0/empty).
    local release_line=""
    if [ -n "$release" ]; then
        release_line="PACKAGE RELEASE: ${release}"$'\n'
    fi

    # Write the manifest
    cat > "$manifest" << EOF
PACKAGE NAME: ${name}-${version}
PACKAGE VERSION: ${version}
${release_line}UNCOMPRESSED SIZE: ${uncompressed_human} (${uncompressed_size} bytes)
BUILD DATE: $(date -u '+%Y-%m-%dT%H:%M:%SZ')
BUILD SYSTEM: InterGenOS LFS 13.0
DESCRIPTION:
${name}: ${description}

FILE LIST:
${file_list}
EOF

    pkg_log "Manifest written: ${manifest} ($(echo "$file_list" | wc -l) entries)"
    return 0
}

# ============================================================================
# pkg_archive — Create a .igos.tar.gz archive from staged files
#
# Usage: pkg_archive <name> <version>
#
# Creates: $IGOS_PKG_ARCHIVES/<name>-<version>.igos.tar.gz
#
# Uses gzip during initial build (available from Chapter 7).
# Archives can be re-compressed to zstd later if desired.
# ============================================================================

pkg_archive() {
    local name="$1"
    local version="$2"
    local dest="${IGOS_PKG_STAGING}/${name}-${version}"
    local archive="${IGOS_PKG_ARCHIVES}/${name}-${version}.igos.tar.gz"

    if [ ! -d "$dest" ]; then
        pkg_error "No staging directory found for ${name}-${version}"
        return 1
    fi

    # Emit a canonical .PKGINFO so the archive is self-describing — the binary
    # repo index (pkm.repo.generate_index) is built from it. Historically only
    # the Python builder wrote .PKGINFO, so legacy-bash-built core/base archives
    # were metadata-less and invisible to the index. Needs python3 + PyYAML + a
    # package.yml recipe OR --fallback-tier (below). The early LFS-Ch8 core
    # packages archived BEFORE PyYAML lands in the chroot (man-pages onward, up to
    # the ch8 `pyyaml` build) get no .PKGINFO here (the python3+PyYAML guard skips
    # this call) and are populated by the in-build post-PyYAML backfill
    # (chroot-build-ch8.sh step 8.88), then enforced universally by the
    # build-squashfs Step 4.7 sweep.
    # scripts/inject-pkginfo.py is a pure post-build loud DETECTOR now (a
    # non-empty inject = a reported gate-escape), not the backfill.
    # --fallback-tier core is passed UNCONDITIONALLY: gen-pkginfo.py uses a
    # matched recipe's real tier when one exists (it only consults the fallback
    # when find_recipe returns None), so recipe-BEARING archives keep their true
    # tier and recipe-LESS LFS-Ch8 core packages get the minimal core .PKGINFO.
    # The python3 + PyYAML + -f guard above is what keeps the loud-fail below
    # safe. gen-pkginfo REQUIRES PyYAML, and python3 here is the LFS Ch7
    # temporary-tools interpreter — it lands in the chroot BEFORE PyYAML is built
    # (ch8 `pyyaml` pass-1, after man-pages). So the guard tests `import yaml` in
    # ISOLATED mode (`python3 -I`), NOT just `command -v python3`, to detect the
    # pre-PyYAML window. The `-I` is load-bearing: a plain `python3 -c 'import
    # yaml'` prepends the build cwd to sys.path, and the `pyyaml`/`pyyaml-pass2`
    # source trees contain a `yaml/` dir that imports as a namespace package — so
    # a cwd-sensitive probe FALSELY reports yaml present *in those very dirs* and
    # fires gen-pkginfo, which (run script-dir-relative, cwd NOT on its path) sees
    # the real absent SYSTEM yaml and rc=3's → halt. `-I` strips cwd/PYTHONPATH/
    # user-site so the probe matches gen-pkginfo's authoritative view of the
    # installed system yaml. In that pre-PyYAML window the call is skipped exactly
    # as for the recipe-less pre-python packages; those archives are backfilled
    # post-PyYAML at step 8.88 and enforced universally by build-squashfs Step
    # 4.7. A failure when the guard DOES fire is therefore a genuine fault (a
    # write error, or a malformed recipe) — exactly what should fail the build
    # rather than ship a metadata-less archive (PI-12). (History: the python3-only
    # guard false-aborted man-pages — python3 present, PyYAML not yet; then the
    # cwd-sensitive `import yaml` guard false-aborted pyyaml itself via its own
    # `yaml/` source dir. Both fixed by `-I`; see git log.)
    local gen_pkginfo_ran=0
    if command -v python3 >/dev/null 2>&1 \
       && python3 -I -c 'import yaml' >/dev/null 2>&1 \
       && [ -f /mnt/intergenos/scripts/gen-pkginfo.py ]; then
        if ! python3 /mnt/intergenos/scripts/gen-pkginfo.py \
            --name "$name" --version "$version" --files-dir "$dest" \
            --repo-root /mnt/intergenos --fallback-tier core; then
            pkg_error "gen-pkginfo failed for ${name}-${version} — .PKGINFO not emitted"
            return 1
        fi
        gen_pkginfo_ran=1
    fi

    # Seal the recipe's lifecycle functions into the staging tree as
    # .scripts/<event>.sh, so they travel INSIDE the signed archive and pkm
    # fires them at install time (pkm/hooks.py run_archive_lifecycle_hook).
    # Without this seam a recipe's post_install() only ever ran here, in the
    # build chroot, and a target installed from archives never received it.
    #
    # Same guard shape as gen-pkginfo above and the same reason: this is a
    # python3 helper, and the early LFS-Ch8 packages archive before a usable
    # interpreter exists. Those packages declare no lifecycle functions, so
    # skipping them seals nothing that was going to be sealed. A failure when
    # the guard DOES fire is a genuine refusal — hookseal fails closed on a
    # function it cannot extract trustworthily, and a truncated hook that
    # appears to succeed is worse than no hook at all.
    local _hook_build_sh="${PKG_TEMPLATE_DIR:-}/build.sh"
    if command -v python3 >/dev/null 2>&1 \
       && [ -n "${PKG_TEMPLATE_DIR:-}" ] && [ -f "$_hook_build_sh" ] \
       && [ -f /mnt/intergenos/igos-build/hookseal.py ]; then
        if ! python3 /mnt/intergenos/igos-build/hookseal.py \
            --staging "$dest" --build-sh "$_hook_build_sh" \
            --name "$name" --version "$version"; then
            pkg_error "hookseal refused ${name}-${version} — lifecycle hook not sealed"
            return 1
        fi
    fi

    # Archive-time ELF word-size audit (RT-1, GE gate re-site): assert every
    # ELF object in the staging tree matches the recipe's elf_class contract
    # (default 64) BEFORE the file set is sealed. Enforcement lives in
    # igos-build/elfaudit.py (stdlib-only — the SAME predicate the Python
    # builder runs in-process, so both builder paths enforce one contract).
    # Guarded on python3 alone (no PyYAML needed): the handful of pre-python
    # early-Ch8 archives are covered by the universal build-squashfs
    # NEEDED/class backstop sweep, the same layering as the .PKGINFO guard
    # above. A failure when the guard fires is a genuine wrong-width object —
    # exactly what must fail the build rather than seal into an archive.
    # Resolve the recipe's elf_class contract. The PACKAGE NAME and the
    # recipe DIRECTORY differ for the *-core packages (dir gcc-core, name
    # gcc) — a name-only glob silently misses the recipe and defaults to
    # 64, which made the width audit refuse the multilib gcc-core's
    # legitimate /usr/lib32 runtime despite its governed elf_class: mixed
    # declaration (GE-01 launch-7 halt L8). The driver exports
    # PKG_TEMPLATE_DIR per package — prefer it; the name glob stays as the
    # fallback for callers without the export.
    local elf_expected="64"
    local -a elf_exempt_args=()
    local recipe_yml
    for recipe_yml in ${PKG_TEMPLATE_DIR:+"${PKG_TEMPLATE_DIR}/package.yml"} /mnt/intergenos/packages/*/"$name"/package.yml; do
        if [ -f "$recipe_yml" ]; then
            local declared
            declared="$(sed -n 's/^elf_class:[[:space:]]*["'\'']\{0,1\}\([a-z0-9]*\)["'\'']\{0,1\}.*/\1/p' "$recipe_yml" | head -1)"
            [ -n "$declared" ] && elf_expected="$declared"
            # elf_class_exempt (L9): root-relative globs covering INERT
            # foreign-width payload (e.g. go's src testdata fixtures).
            # Top-level YAML list only — the key line, then `- "glob"`
            # entries until the first non-list line. elfaudit.py reports
            # every exempted file loudly and REFUSES a glob that exempts
            # nothing (stale declaration).
            local exempt_glob
            while IFS= read -r exempt_glob; do
                [ -n "$exempt_glob" ] && elf_exempt_args+=(--exempt "$exempt_glob")
            done < <(awk '
                /^elf_class_exempt:/ { in_list=1; next }
                in_list && /^[[:space:]]+-[[:space:]]*/ {
                    sub(/^[[:space:]]+-[[:space:]]*/, "")
                    gsub(/["'"'"']/, "")
                    sub(/[[:space:]]*#.*$/, "")
                    if (length($0)) print
                    next
                }
                in_list { in_list=0 }
            ' "$recipe_yml")
            break
        fi
    done
    if command -v python3 >/dev/null 2>&1; then
        # python3 present but a predicate missing = a recipe-tree sync
        # failure, never a legitimate bootstrap state — halt rather than
        # seal an unaudited archive (fail-closed; the python3-ABSENT
        # window below is the only sanctioned skip).
        if [ ! -f /mnt/intergenos/igos-build/elfaudit.py ]; then
            pkg_error "elfaudit.py missing while python3 is present — recipe-tree sync failure; refusing to archive ${name}-${version} unaudited"
            return 1
        fi
        # Audit stderr is captured and re-emitted through pkg_log: three
        # launch-gate refusals in a row (L7 time64, L8 width) were invisible
        # in every log the watch procedure reads — a gate that halts must
        # halt LOUDLY in the orchestrator log, not only on a lost stderr.
        local audit_out
        audit_out=$(python3 /mnt/intergenos/igos-build/elfaudit.py \
            --root "$dest" --expected "$elf_expected" --name "$name" \
            ${elf_exempt_args[@]+"${elf_exempt_args[@]}"} 2>&1)
        if [ $? -ne 0 ]; then
            pkg_log "$audit_out"
            pkg_error "ELF-class audit refused the archive for ${name}-${version} (elf_class=${elf_expected})"
            return 1
        fi
        [ -n "$audit_out" ] && pkg_log "$audit_out"
        # Archive-time time64 build-log assertion (RT-8, GE gate): a 32-bit
        # package must never enable 64-bit time_t — an upstream opting itself
        # into _TIME_BITS=64 skews public struct layouts against the time32
        # ABI prebuilt game binaries use (silent memory corruption, not an
        # error path). Scans THIS package's per-package build logs via the
        # shared predicate igos-build/time64audit.py (same one the Python
        # builder runs in-process); the predicate fail-closes when no log is
        # readable. 64-bit packages skip here (the define is their correct
        # ABI); mixed announces its waiver inside the predicate.
        if [ "$elf_expected" != "64" ]; then
            if [ ! -f /mnt/intergenos/igos-build/time64audit.py ]; then
                pkg_error "time64audit.py missing while python3 is present — recipe-tree sync failure; refusing to archive ${name}-${version} unaudited"
                return 1
            fi
            local t64_args=(--name "$name" --expected "$elf_expected")
            local t64_log
            for t64_log in "${IGOS_LOGS}/${name}-"*.log; do
                [ -f "$t64_log" ] && t64_args+=(--log "$t64_log")
            done
            # Same loud-refusal routing as the width audit above (L7's
            # refusal text reached no log the watch procedure reads).
            local t64_out
            t64_out=$(python3 /mnt/intergenos/igos-build/time64audit.py "${t64_args[@]}" 2>&1)
            if [ $? -ne 0 ]; then
                pkg_log "$t64_out"
                pkg_error "time64 audit refused the archive for ${name}-${version} (elf_class=${elf_expected})"
                return 1
            fi
            [ -n "$t64_out" ] && pkg_log "$t64_out"
        fi
    else
        # The pre-python bootstrap window (earliest Ch8 packages, before
        # the Ch7 temp interpreter lands): the ONLY sanctioned skip, said
        # loudly, and covered downstream by the build-squashfs
        # NEEDED-closure + word-size backstop sweep. A 32-bit package in
        # this window is NOT a sanctioned state — no lib32 package may
        # build before python lands; halt rather than skip its audits.
        if [ "$elf_expected" = "32" ]; then
            pkg_error "elf_class=32 package ${name}-${version} in the pre-python bootstrap window — its width/time64 audits cannot run; refusing to archive unaudited"
            return 1
        fi
        pkg_log "ELF-class audit skipped for ${name}-${version} (pre-python bootstrap window; the build-squashfs backstop sweep covers it)"
    fi

    # Runtime-dir gate (2026-07-17): an archive must never carry /var/run or
    # /var/lock as REAL directories, nor any /usr/var tree. On installed
    # systems /var/run + /var/lock are symlinks into /run (base-files r9); a
    # real-dir member extracted before the symlink lands materializes a real
    # dir that systemd-tmpfiles cannot replace -> split-brain runtime dirs
    # (the class measured on installs 2026-07-17: 6 offender archives).
    # base-files itself ships the two SYMLINKS — symlink members pass.
    for _rt in var/run var/lock; do
        if [ -d "$dest/$_rt" ] && [ ! -L "$dest/$_rt" ]; then
            pkg_error "runtime-dir gate: ${name}-${version} stages $_rt as a REAL directory — never ship runtime dirs (use a tmpfiles.d entry; strip the dir in do_install)"
            return 1
        fi
    done
    if [ -e "$dest/usr/var" ]; then
        pkg_error "runtime-dir gate: ${name}-${version} stages usr/var — state under /usr means a localstatedir misconfiguration (default \${prefix}/var); configure with --localstatedir=/var"
        return 1
    fi

    # Create the archive — rooted at the staging directory so paths are relative
    # This means extracting to / will put files in the right place
    tar -C "$dest" -czf "$archive" .

    local rc=$?
    if [ $rc -ne 0 ]; then
        pkg_error "Archive creation failed for ${name}-${version}"
        return 1
    fi

    # PI-12 (2A): when gen-pkginfo ran above (python3 present), assert the
    # produced archive is self-describing — a well-formed ./.PKGINFO carrying at
    # least pkgname/pkgver/pkgrel. Gated on the SAME predicate as the emit
    # (assert only what we attempted to write): the recipe-less core packages
    # built before python lands in the Ch8 chroot (glibc/binutils/gcc/… —
    # command -v python3 false there) legitimately have no .PKGINFO at this
    # point, so asserting unconditionally would false-abort at the first one
    # (glibc-core). The pre-python recipe-less archives are populated downstream
    # (once python3 exists) and coverage is enforced universally by the
    # build-squashfs.sh Step 4.7 sweep before seal.
    if [ "$gen_pkginfo_ran" = 1 ]; then
        local pkginfo
        pkginfo="$(tar -xzOf "$archive" ./.PKGINFO 2>/dev/null)" || pkginfo=""
        if ! printf '%s\n' "$pkginfo" | grep -qE '^pkgname=' \
           || ! printf '%s\n' "$pkginfo" | grep -qE '^pkgver=' \
           || ! printf '%s\n' "$pkginfo" | grep -qE '^pkgrel='; then
            pkg_error "archive ${archive} lacks a well-formed .PKGINFO (need pkgname/pkgver/pkgrel)"
            return 1
        fi
    fi

    local archive_size
    archive_size=$(du -sh "$archive" | cut -f1)
    pkg_log "Archive created: ${archive} (${archive_size})"

    # Update manifest with compressed size
    local manifest="${IGOS_PKG_DB}/${name}-${version}"
    if [ -f "$manifest" ]; then
        local compressed_bytes
        compressed_bytes=$(stat -c%s "$archive")
        sed -i "/^BUILD DATE:/i COMPRESSED SIZE: ${archive_size} (${compressed_bytes} bytes)" "$manifest"
    fi

    return 0
}

# ============================================================================
# pkg_deploy — Copy staged files to the live filesystem
#
# Usage: pkg_deploy <name> <version>
#
# Copies everything from the staging directory to /
# Preserves permissions, ownership, and symlinks
#
# Safety: pre-checks for top-level entries that would collide with root-level
# symlinks (lib -> usr/lib, bin -> usr/bin, etc.). A package staging a real
# directory over one of these symlinks would kill the system.
# ============================================================================

pkg_deploy() {
    local name="$1"
    local version="$2"
    local dest="${IGOS_PKG_STAGING}/${name}-${version}"

    if [ ! -d "$dest" ]; then
        pkg_error "No staging directory found for ${name}-${version}"
        return 1
    fi

    # Pre-deploy safety check: detect staging entries that would collide with
    # root-level symlinks. These symlinks (lib -> usr/lib, bin -> usr/bin, etc.)
    # are load-bearing — replacing them with real directories is catastrophic.
    local dangerous=""
    for entry in lib lib64 bin sbin; do
        if [ -d "${dest}/${entry}" ] && [ ! -L "${dest}/${entry}" ] && [ -L "/${entry}" ]; then
            dangerous="${dangerous} ${entry}"
        fi
    done

    if [ -n "$dangerous" ]; then
        pkg_error "DANGEROUS: ${name}-${version} staging contains top-level dirs" \
                  "that would collide with root symlinks:${dangerous}"
        pkg_error "Fix the package build.sh to install to usr/ paths instead"
        return 1
    fi

    pkg_log "Deploying ${name}-${version} to live filesystem"

    # Use tar for deployment:
    # --no-overwrite-dir    preserves metadata of existing real directories
    # --keep-directory-symlink  follows existing symlinks to directories instead
    #                           of replacing them (e.g., /var/run -> /run)
    # --exclude='./.PKGINFO'  .PKGINFO is archive metadata (pkg_archive writes
    #                         it into staging so the tarball is self-describing
    #                         for the repo index) — it is NOT payload. Deploying
    #                         it put an untracked, unverified /.PKGINFO on the
    #                         live root that every later package overwrote.
    #                         Same exclusion as the Python tracker's pkg_deploy.
    # --exclude='./.scripts'  same class as .PKGINFO: the sealed lifecycle
    #                         hooks are archive metadata pkm fires out of the
    #                         extracted staging dir, never payload. Deploying
    #                         them would put an unowned /.scripts/ on the root.
    tar -C "${dest}" --exclude='./.PKGINFO' --exclude='./.scripts' -cf - . \
        | tar -C / -xf - --no-overwrite-dir --keep-directory-symlink

    local rc=$?
    if [ $rc -ne 0 ]; then
        pkg_error "Deploy failed for ${name}-${version}"
        return 1
    fi

    # Setuid/setgid/sticky safety-net. The tar pipeline above SHOULD preserve
    # these bits when running as root. Empirically (May 16 2026 verification)
    # it does. But the May 12 2026 deploy of polkit/util-linux/shadow/sudo
    # dropped setuid on every binary they ship (pkexec, su, sudo, mount,
    # umount, passwd, chfn, chsh, newgrp, gpasswd, chage, expiry,
    # newuidmap, newgidmap), discovered when Forge GUI elevation failed in
    # the cycle-2 smoke test with "pkexec must be setuid root". Root cause
    # not pinpointed to a specific stripping operation. This loop closes the
    # loop regardless: re-applies any setuid/setgid/sticky bit present in
    # the package's staging directory to the deployed file. Idempotent —
    # if the tar pipeline preserved correctly, this is a no-op.
    while IFS= read -r -d '' staged_file; do
        local rel="${staged_file#${dest}}"
        local deployed_file="/${rel}"
        if [ -f "$deployed_file" ]; then
            local staged_mode
            staged_mode=$(stat -c '%a' "$staged_file" 2>/dev/null || echo "")
            if [ -n "$staged_mode" ] && [ "${#staged_mode}" -eq 4 ]; then
                # 4-digit mode means setuid/setgid/sticky present in high bit
                local deployed_mode
                deployed_mode=$(stat -c '%a' "$deployed_file" 2>/dev/null || echo "")
                if [ "$deployed_mode" != "$staged_mode" ]; then
                    chmod "$staged_mode" "$deployed_file"
                    pkg_log "  setuid-restore: ${deployed_file} -> ${staged_mode}"
                fi
            fi
        fi
    done < <(find "$dest" -type f \( -perm -4000 -o -perm -2000 -o -perm -1000 \) -print0)

    pkg_log "Deployed ${name}-${version}"
    return 0
}

# ============================================================================
# pkg_cleanup — Remove staging directory after successful install
#
# Usage: pkg_cleanup <name> <version>
# ============================================================================

# ---------------------------------------------------------------------------
# pkg_hook_baseline / pkg_record_hook_changes — give the source-build lane the
# same evidence about its own post_install that the archive install path has.
#
# WHY THIS EXISTS. A package whose own sealed hook rewrites one of its own
# payload files in place — docbook-xml's catalog.xml, rewritten by xmlcatalog,
# is the live example — must have that file recorded as hook-managed content:
# existence checked, the byte comparison against the payload hash skipped,
# because the recorded hash is the pre-hook one and can never match again. The
# archive install path learns this by snapshotting around the hook it runs
# itself. This lane cannot: pkg_install writes the manifest from the PRISTINE
# staging tree and runs `pkm import` before the driver ever reaches
# post_install, so its rows recorded pre-hook bytes with nothing marking them,
# and the ISO metadata-sync gate byte-compared a correct image and refused it.
#
# These two halves close that. The baseline is captured immediately BEFORE the
# recipe's post_install runs and the comparison immediately AFTER, so a change
# is attributed to that window rather than inferred from a file disagreeing
# with its recorded hash — inference would re-bless a damaged file, which is
# the opposite of what this is for. Both halves are scoped to the package's own
# rows, so a hook that writes a file another package owns keeps the existing
# reported-never-absorbed treatment.
PKG_HOOK_BASELINE_FILE=""

pkg_hook_baseline() {
    local name="$1"
    PKG_HOOK_BASELINE_FILE=""
    command -v pkm >/dev/null 2>&1 || return 0
    # A pkm without this subcommand is an older one in a partially built
    # chroot. Skip QUIETLY only here, where the feature genuinely does not
    # exist yet; every other failure below is reported.
    pkm hook-baseline --help >/dev/null 2>&1 || return 0

    local baseline
    baseline="$(mktemp -t "pkm-hook-baseline-${name}.XXXXXX")" || {
        pkg_log "  WARNING: could not create a hook baseline file for ${name} — its post_install changes will NOT be recorded"
        return 0
    }
    if pkm hook-baseline "$name" --out "$baseline" >/dev/null 2>&1; then
        PKG_HOOK_BASELINE_FILE="$baseline"
    else
        rm -f "$baseline"
        pkg_log "  WARNING: pkm hook-baseline failed for ${name} — its post_install changes will NOT be recorded"
    fi
    return 0
}

pkg_record_hook_changes() {
    local name="$1"
    [ -n "$PKG_HOOK_BASELINE_FILE" ] || return 0
    [ -f "$PKG_HOOK_BASELINE_FILE" ] || { PKG_HOOK_BASELINE_FILE=""; return 0; }

    # Reported either way. A recording failure leaves files on disk whose
    # classification the shipped image will get wrong, and a warning that
    # scrolls past is the only notice anyone gets, so it says what was lost.
    if ! pkm record-hook-changes "$name" --baseline "$PKG_HOOK_BASELINE_FILE" 2>&1 | sed 's/^/  /'; then
        pkg_log "  WARNING: pkm record-hook-changes failed for ${name} — any file its post_install rewrote stays recorded as ordinary payload"
    fi
    rm -f "$PKG_HOOK_BASELINE_FILE"
    PKG_HOOK_BASELINE_FILE=""
    return 0
}

pkg_cleanup() {
    local name="$1"
    local version="$2"
    local dest="${IGOS_PKG_STAGING}/${name}-${version}"

    rm -rf "$dest"
}

# ============================================================================
# pkg_run_tests — Run a test suite under the project's allow-list policy.
#
# Usage: pkg_run_tests <package.yml> <test_cmd> [args...]
#
# Reads the optional `tests:` block from the given package.yml:
#
#   tests:
#     enabled: true                        # default; false = skip phase
#     failure_policy: strict|known_failures # default strict; halts on any fail
#     reason: "..."                        # required when enabled=false or
#                                          #   failure_policy=known_failures
#
# Behavior:
#   - No `tests:` block → strict mode (any test failure halts).
#   - tests.enabled=false → skip silently with a log line. Reason required.
#   - failure_policy=strict (default) → run command, halt on non-zero exit.
#   - failure_policy=known_failures → run command, log a warning on non-zero
#     exit but return 0. Reason required and printed to log.
#
# Spec: docs/test-allow-list.md
# Adopted: 2026-05-08 after Build #5 audit.
# ============================================================================

pkg_run_tests() {
    local pkg_yml="$1"
    shift
    local cmd=("$@")

    if [ ! -f "$pkg_yml" ]; then
        echo "[tests] error: package.yml not found at $pkg_yml" >&2
        return 1
    fi

    # Parse the tests: block. We only honor exact keys at indent level 2,
    # under a top-level 'tests:' key. This matches the rest of the project's
    # bash-friendly YAML conventions (no full YAML parser).
    local enabled policy reason
    enabled=$(awk '/^tests:[[:space:]]*$/{f=1; next} /^[A-Za-z_]+:/{f=0} f && /^[[:space:]]+enabled:[[:space:]]*/{sub(/^[[:space:]]+enabled:[[:space:]]*/,""); gsub(/[[:space:]]+$/,""); print; exit}' "$pkg_yml")
    policy=$(awk '/^tests:[[:space:]]*$/{f=1; next} /^[A-Za-z_]+:/{f=0} f && /^[[:space:]]+failure_policy:[[:space:]]*/{sub(/^[[:space:]]+failure_policy:[[:space:]]*/,""); gsub(/[[:space:]]+$/,""); print; exit}' "$pkg_yml")
    reason=$(awk '/^tests:[[:space:]]*$/{f=1; next} /^[A-Za-z_]+:/{f=0} f && /^[[:space:]]+reason:[[:space:]]*/{sub(/^[[:space:]]+reason:[[:space:]]*/,""); gsub(/^"/,""); gsub(/"$/,""); print; exit}' "$pkg_yml")

    enabled="${enabled:-true}"
    policy="${policy:-strict}"

    if [ "$enabled" = "false" ]; then
        if [ -z "$reason" ]; then
            echo "[tests] error: tests.enabled=false but no reason given in $pkg_yml" >&2
            return 1
        fi
        echo "[tests] phase skipped (enabled=false). Reason: $reason"
        return 0
    fi

    if [ "$policy" != "strict" ] && [ "$policy" != "known_failures" ]; then
        echo "[tests] error: invalid failure_policy '$policy' in $pkg_yml (expected strict|known_failures)" >&2
        return 1
    fi

    if [ "$policy" = "known_failures" ] && [ -z "$reason" ]; then
        echo "[tests] error: failure_policy=known_failures requires a reason in $pkg_yml" >&2
        return 1
    fi

    echo "[tests] policy=$policy"
    [ -n "$reason" ] && echo "[tests] reason: $reason"
    echo "[tests] running: ${cmd[*]}"

    # The `|| rc=$?` form keeps the policy-check below reachable when this
    # function is called from a context with `set -e` (errexit) active —
    # which is the norm: chroot-build-{ch8,core-extra}.sh, every package
    # build.sh's check() function, and the Python-builder's bash shell all
    # set -e. Without `||`, errexit would kill pkg_run_tests at the
    # `"${cmd[@]}"` line on test-command failure, BEFORE the
    # `failure_policy=known_failures` branch could suppress it. Build #6
    # Halt #5 (FLAC, exit 2) was that exact failure mode.
    local rc=0
    "${cmd[@]}" || rc=$?

    if [ $rc -eq 0 ]; then
        echo "[tests] PASSED"
        return 0
    fi

    if [ "$policy" = "known_failures" ]; then
        echo "[tests] warning: test suite exit $rc — allowed by failure_policy=known_failures"
        echo "[tests] warning reason: $reason"
        return 0
    fi

    # Truth-in-logging (launch-7, 2026-07-03): this helper REPORTS the
    # failure; whether it halts is the CALLER'S policy. The bash tier
    # drivers deliberately treat check() as informational-NON-FATAL
    # (LFS-consistent; see chroot-build-base.sh's CHECK block), so the old
    # "halting build" wording here was a lie in the log — the time
    # package's max-rss test failure printed 'halting' and the build
    # correctly continued. A message must never claim an enforcement the
    # plumbing does not deliver.
    echo "[tests] FAILED (exit $rc) — strict policy: failure reported to the caller (the driver's check policy decides fatality)" >&2
    return $rc
}

# ============================================================================
# pkg_assert_known_test_failures — validated known-failure allow-list.
#
# Usage (from a package build.sh check() function):
#   check() {
#       local log; log="$(mktemp)"; local rc=0
#       make check > "$log" 2>&1 || rc=$?
#       cat "$log"                              # preserve full output to pkg log
#       pkg_assert_known_test_failures "$rc" "$log" "test/cp.test"  # ONLY allowed fail
#   }
#
# First arg is the check command's exit code: rc==0 means the suite passed and
# the helper returns 0 immediately (nothing to validate). Only a non-zero rc
# triggers failure-line parsing + allow-list comparison.
#
# Parses automake/TAP failure lines ("FAIL: <name>" / "ERROR: <name>") from
# <test_log> and compares them against the declared expected-failure patterns
# (substring match). Behavior:
#   - zero failures, OR every failure matches an expected pattern  -> return 0
#     (build continues; the documented known failure is accepted).
#   - ANY failure not in the allow-list, OR a non-zero check with no parseable
#     FAIL lines (i.e. a non-test failure)  -> emit a build_failure trace event,
#     print the offending failures, and `exit 1` to HALT the build.
#
# WHY this exists (2026-06-01, security-first posture — no blanket masking): the
# blanket "[CHECK] … NON-FATAL" wrapper and `make check || true` patterns silently
# accept EVERY test failure, which is exactly the unverified-claim class a
# superhuman adversary exploits. This converts "ignore all failures" into "accept
# ONLY the specific, documented failures; halt on anything new." It `exit`s
# (not `return`s) on the unexpected case so the allow-list is authoritative
# regardless of how the calling wrapper treats check()'s return code.
#
# NOTE: gcc/binutils/glibc carry large, environment-dependent, non-enumerable
# expected-failure sets and are NOT candidates for an exact allow-list — those
# stay on pkg_run_tests' documented `known_failures` policy. Use this helper for
# packages whose expected failures are a small, named, documented set (e.g. acl's
# test/cp.test per LFS 13.0 §8.5). Migrating the remaining blanket/`|| true`
# packages to either this helper or pkg_run_tests is progressive, deliberate work
# (NO THRASHING, Rule A) — not a single big-bang flip.
pkg_assert_known_test_failures() {
    local check_rc="$1"; local test_log="$2"; shift 2
    local -a expected=("$@")

    # Suite passed — nothing to validate.
    [[ "$check_rc" =~ ^[0-9]+$ ]] && [ "$check_rc" -eq 0 ] && return 0

    if [ ! -r "$test_log" ]; then
        echo "[tests] error: test log unreadable: $test_log — cannot validate failures, HALTING" >&2
        [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && build_failure_emit \
            --where "pkg_assert_known_test_failures" --why "test log unreadable: $test_log"
        exit 1
    fi

    # Collect actual failing test names (automake "FAIL:/ERROR:" lines).
    local -a actual=()
    local line
    while IFS= read -r line; do
        [ -n "$line" ] && actual+=("$line")
    done < <(grep -E '^(FAIL|ERROR): ' "$test_log" 2>/dev/null | sed -E 's/^(FAIL|ERROR): //' | sort -u)

    if [ ${#actual[@]} -eq 0 ]; then
        # No parseable FAIL lines. If we got here the caller saw a non-zero check;
        # a check failure with no test-result failures is a real (non-test) error.
        echo "[tests] no known-failure allow-list match: zero parseable FAIL lines but check reported failure — treating as REAL failure, HALTING" >&2
        [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && build_failure_emit \
            --where "pkg_assert_known_test_failures" --why "check failed with no parseable FAIL lines (non-test failure)"
        exit 1
    fi

    local -a unexpected=()
    local a e ok
    for a in "${actual[@]}"; do
        ok=0
        for e in "${expected[@]}"; do
            case "$a" in *"$e"*) ok=1; break ;; esac
        done
        [ $ok -eq 0 ] && unexpected+=("$a")
    done

    if [ ${#unexpected[@]} -eq 0 ]; then
        echo "[tests] OK: all ${#actual[@]} failure(s) are in the declared known-failure allow-list:"
        printf '[tests]   (allowed) %s\n' "${actual[@]}"
        return 0
    fi

    echo "[tests] HALT: ${#unexpected[@]} test failure(s) NOT in the known-failure allow-list:" >&2
    printf '[tests]   (UNEXPECTED) %s\n' "${unexpected[@]}" >&2
    echo "[tests] declared allow-list: ${expected[*]:-<none>}" >&2
    echo "[tests] An unexpected test failure is a real regression signal — investigate; do not mask." >&2
    [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && build_failure_emit \
        --where "pkg_assert_known_test_failures" \
        --why "unexpected test failure(s): ${unexpected[*]}" \
        --stderr "allow-list: ${expected[*]}"
    exit 1
}

# ============================================================================
# pkg_install — Full pipeline: stage -> manifest -> archive -> deploy -> cleanup
#
# Usage: pkg_install <name> <version> [description]
#
# This is the main entry point called by the build runner after
# configure/build/check have completed.
# ============================================================================

pkg_install() {
    local name="$1"
    local version="$2"
    local description="${3:-No description}"
    # Component A (release honesty): optional 4th arg, passed through to
    # pkg_manifest. All four build drivers supply it from the recipe's
    # package.yml; an absent release still emits no PACKAGE RELEASE header, so
    # a 3-arg caller keeps the legacy shape.
    local release="${4:-}"

    pkg_log ">>> Installing package: ${name}-${version}"

    local start
    start=$(date +%s)

    # Ensure database directories exist
    pkg_init

    # Stage / manifest / archive / deploy chain.
    #
    # IMPORTANT: each step runs with the rc captured AFTER the call,
    # NOT via `func || return 1`. The `||` form suspends errexit through
    # the entire body of the called function recursively (bash(1):
    # "When a compound command or shell function executes in a context
    # where -e is being ignored, none of the commands executed within
    # the compound command or function body will be affected by the -e
    # setting"). That suspension is the FIX-THIS-ASAP silent-loss class
    # hazard (chroot-build-{ch8,base,core-extra}.sh outermost-||
    # refactor 2026-05-24). For these inner steps, the same hazard
    # applies — pkg_stage / pkg_manifest / pkg_archive / pkg_deploy each
    # invoke build.sh-defined do_install + post_install with set -e at
    # the top; if THIS layer used `||`, those `set -e` declarations
    # become no-ops and an apparmor-class silent partial-install ships.
    # Two-statement form keeps errexit honored end-to-end.
    local _pi_start_ms
    _pi_start_ms=$(date +%s%3N)

    pkg_stage "$name" "$version"
    local stage_rc=$?
    if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
        local _stage_dir="${IGOS_PKG_STAGING}/${name}-${version}"
        local _files=0 _bytes=0
        if [ -d "$_stage_dir" ]; then
            _files=$(find "$_stage_dir" -type f 2>/dev/null | wc -l)
            _bytes=$(du -sb "$_stage_dir" 2>/dev/null | cut -f1)
        fi
        trace_event pkg_destdir_done pkg="$name" version="$version" destdir="$_stage_dir" \
            file_count::=${_files:-0} size_bytes::=${_bytes:-0} rc::=$stage_rc
    fi
    if [ "$stage_rc" -ne 0 ]; then return 1; fi

    pkg_manifest "$name" "$version" "$description" "$release"
    local manifest_rc=$?
    if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
        local _manifest_path="${IGOS_PKG_DB}/${name}-${version}"
        local _msha=""
        [ -f "$_manifest_path" ] && _msha=$(sha256sum "$_manifest_path" 2>/dev/null | cut -d' ' -f1)
        trace_event pkg_manifest_emit pkg="$name" version="$version" \
            manifest_path="$_manifest_path" sha="$_msha" rc::=$manifest_rc
    fi
    if [ "$manifest_rc" -ne 0 ]; then return 1; fi

    pkg_archive "$name" "$version"
    local archive_rc=$?
    if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
        local _archive_path="${IGOS_PKG_ARCHIVES}/${name}-${version}.igos.tar.gz"
        local _asize=0 _asha=""
        if [ -f "$_archive_path" ]; then
            _asize=$(stat -c%s "$_archive_path" 2>/dev/null || echo 0)
            _asha=$(sha256sum "$_archive_path" 2>/dev/null | cut -d' ' -f1)
        fi
        trace_event pkg_archive_emit pkg="$name" version="$version" \
            archive_path="$_archive_path" size_bytes::=${_asize:-0} sha="$_asha" rc::=$archive_rc
    fi
    if [ "$archive_rc" -ne 0 ]; then return 1; fi

    [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_event pkg_deploy_start pkg="$name" version="$version" destdir="${IGOS_PKG_STAGING}/${name}-${version}" system_root="/"
    local _deploy_start_ms
    _deploy_start_ms=$(date +%s%3N)
    pkg_deploy "$name" "$version"
    local deploy_rc=$?
    if [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ]; then
        local _deploy_dur=$(( $(date +%s%3N) - _deploy_start_ms ))
        trace_event pkg_deploy_end pkg="$name" version="$version" rc::=$deploy_rc duration_ms::=$_deploy_dur
    fi
    if [ "$deploy_rc" -ne 0 ]; then return 1; fi

    # Register in pkm SQLite database. Mirrors the Python orchestrator's
    # tracker.py:pkg_register_pkm_db gate-3 step. Pre-fix (2026-05-16), the
    # bash pkg_install wrote the text manifest + archive but never wrote
    # SQLite, leaving 236 packages "phantom-installed" — files + manifest +
    # archive on disk, but `pkm provides <file>` / `pkm info <name>` /
    # `pkm files <name>` blind. Discovered when /usr/bin/ping triaged as
    # an orphan binary (inetutils owned it but pkm didn't know inetutils
    # was installed). `pkm import` reads the manifest we just wrote and
    # registers it in SQLite. Idempotent — re-running on an already-
    # registered package is a no-op.
    if command -v pkm >/dev/null 2>&1; then
        [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_event pkm_invoke subcommand=import pkg="$name" version="$version" cwd="$PWD"
        pkg_run_pkm_single_flight import
    fi

    # Clean up staging directory
    pkg_cleanup "$name" "$version"

    local elapsed=$(( $(date +%s) - start ))
    pkg_log "Package ${name}-${version} installed successfully (${elapsed}s)"
    pkg_log ""

    return 0
}

# ============================================================================
# pkg_info — Display information about an installed package
#
# Usage: pkg_info <name>-<version>
#    or: pkg_info (no args — list all installed packages)
# ============================================================================

pkg_info() {
    if [ -z "$1" ]; then
        # List all installed packages
        if [ -d "$IGOS_PKG_DB" ]; then
            for manifest in "$IGOS_PKG_DB"/*; do
                [ -f "$manifest" ] || continue
                local pkg_name pkg_version
                pkg_name=$(grep "^PACKAGE NAME:" "$manifest" | cut -d: -f2- | tr -d ' ')
                pkg_version=$(grep "^PACKAGE VERSION:" "$manifest" | cut -d: -f2- | tr -d ' ')
                local desc
                desc=$(grep "^${pkg_name%%"-$pkg_version"}:" "$manifest" | head -1)
                echo "${pkg_name}  ${desc:+— $desc}"
            done
        else
            echo "No packages installed."
        fi
    else
        # Show specific package
        local manifest="${IGOS_PKG_DB}/$1"
        if [ -f "$manifest" ]; then
            cat "$manifest"
        else
            echo "Package $1 is not installed."
            return 1
        fi
    fi
}

# ============================================================================
# pkg_files — List files owned by an installed package
#
# Usage: pkg_files <name>-<version>
# ============================================================================

pkg_files() {
    local manifest="${IGOS_PKG_DB}/$1"
    if [ ! -f "$manifest" ]; then
        echo "Package $1 is not installed."
        return 1
    fi

    # Extract file list (everything after "FILE LIST:" line)
    sed -n '/^FILE LIST:$/,$ { /^FILE LIST:$/d; p }' "$manifest"
}

# ============================================================================
# pkg_owner — Find which package owns a file
#
# Usage: pkg_owner /usr/bin/gcc
# ============================================================================

pkg_owner() {
    local target="$1"

    # Strip leading / for comparison against manifest paths
    target="${target#/}"

    if [ -d "$IGOS_PKG_DB" ]; then
        for manifest in "$IGOS_PKG_DB"/*; do
            [ -f "$manifest" ] || continue
            if sed -n '/^FILE LIST:$/,$ p' "$manifest" | grep -qx "$target"; then
                basename "$manifest"
            fi
        done
    fi
}

# ============================================================================
# pkg_remove — Remove an installed package
#
# Usage: pkg_remove <name>-<version>
#
# Removes all files owned by the package (in reverse order so dirs come last),
# then removes the manifest. Does NOT remove the archive.
# ============================================================================

pkg_remove() {
    local pkg="$1"
    local manifest="${IGOS_PKG_DB}/${pkg}"

    if [ ! -f "$manifest" ]; then
        pkg_error "Package ${pkg} is not installed."
        return 1
    fi

    pkg_log "Removing package: ${pkg}"

    # Get file list, reverse sorted (files before their parent directories)
    local files
    files=$(pkg_files "$pkg" | sort -r)

    local removed=0
    local skipped=0

    while IFS= read -r file; do
        [ -z "$file" ] && continue
        local fullpath="/${file}"

        if [ -d "$fullpath" ] && [ ! -L "$fullpath" ]; then
            # Only remove directory if empty
            rmdir "$fullpath" 2>/dev/null && removed=$((removed+1))
        elif [ -e "$fullpath" ] || [ -L "$fullpath" ]; then
            rm -f "$fullpath" && removed=$((removed+1))
        else
            skipped=$((skipped+1))
        fi
    done <<< "$files"

    # Remove the manifest
    rm -f "$manifest"

    pkg_log "Removed ${pkg}: ${removed} files/dirs removed, ${skipped} already absent"
    return 0
}
