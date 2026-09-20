#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# lib32-env.sh — THE single bash-tier lib32 build profile (GE arc, G2/T2).
#
# Every bash-tier lib32-* recipe sources this file at the top of configure()
# and calls the staging helpers in do_install(). Recipes may NOT restate
# these values (T2: one definition in one file — per-recipe env drift is the
# failure mode this profile exists to prevent). The meson-tier twin is
# config/lib32/lib32-cross.ini; keep the two in lockstep.
#
# In-chroot canonical path: /mnt/intergenos/scripts/lib32-env.sh
#
# Notes:
# - PKG_CONFIG_LIBDIR (not _PATH): no 64-bit .pc fallthrough (trap T2).
# - -U_TIME_BITS: the RT-8 scrub half — a lib32 package must never compile
#   with 64-bit time_t (silent struct-layout skew against time32 prebuilt
#   binaries); the archive-time time64 log assertion is the enforcement twin.
# - -mstackrealign is NOT set here: it is the compiler DEFAULT for -m32 SSE
#   via the i386.h sed in the gcc recipes (D-W0-2) — one authoritative
#   mechanism, no dual definitions.
# - The chroot baseline CFLAGS (-march=x86-64-v2 -mtune=generic -O2 -pipe,
#   chroot-enter.sh) stay in force: any x86-64-v2 CPU executes 32-bit code
#   with those features, matching our 64-bit hardware floor.

export CC="gcc -m32 -U_TIME_BITS"
export CXX="g++ -m32 -U_TIME_BITS"
# AR/LD pinned: autotools probes ${host}-ar/${host}-ld for the branded
# i686-igos-linux-gnu triplet, misses (no such prefixed binutils), and warns
# "falling back to ld which may be incorrect" on every lib32-* configure.
# The native tools are correct for -m32 objects; pin them so the probe is
# deterministic, not a fallback. (Post-burn log sweep, 2026-07-11.)
export AR=ar
export LD=ld
# /usr/share/pkgconfig stays searchable: arch-independent .pc files are
# valid for both widths (kept in LOCKSTEP with the pkgconf i686 personality
# and the cross-file wrapper — same answer from all three mechanisms).
export PKG_CONFIG_LIBDIR=/usr/lib32/pkgconfig:/usr/share/pkgconfig

# The native-in-chroot 32-bit host triplet. Multilib-LFS Ch 8 uses the
# generic i686-pc-linux-gnu here; we brand the vendor field (work-plan 1.10,
# operator-flagged 2026-07-04) exactly as the book's $LFS_TGT vendor
# substitution sanctions — which also makes autotools' ${host}-pkg-config
# probe hit pkgconf's branded i686-igos-linux-gnu personality BY DESIGN
# (previously it worked by fallback). Every autotools twin consumes this via
# --host=${LIB32_HOST} (+ igos-build/styles/autotools.py), so this is the
# single definition site.
export LIB32_HOST=i686-igos-linux-gnu

# RT-8 visibility (launch-gate arc, lib32-zstd 2026-07-03): the archive-time
# time64 audit FAIL-CLOSES when no full compiler invocation is visible in
# the build log — an audit that cannot see must refuse, and silent-rules
# make output blinds it. Force verbose compile logging for every make-based
# twin from this ONE profile: GNU make treats var=value entries in MAKEFLAGS
# as command-line overrides, so V=1 + VERBOSE=1 disable automake- and
# zstd-style .SILENT modes (harmless where a Makefile implements neither).
# The meson/cmake twins already run ninja -v / cmake --build --verbose.
# Prior MAKEFLAGS is saved and restored by lib32_env_end / the driver scrub
# (same self-cleaning discipline as the -m32 exports above).
export IGOS_LIB32_PREV_MAKEFLAGS="${MAKEFLAGS-__unset__}"
export MAKEFLAGS="V=1 VERBOSE=1${MAKEFLAGS:+ ${MAKEFLAGS}}"

# Leak marker (Wave-1 adversarial-verify finding W1-a): the trailing lib32_env_end in a
# recipe is SKIPPED when do_install aborts under errexit, so the -m32
# exports could survive in the drivers' shared shell. This marker lets the
# driver-side lib32_env_scrub (pkg-functions.sh, called before EVERY
# package) detect and clear leaked lib32 env no matter how the previous
# package died — the mechanism holds regardless of wiring position.
export IGOS_LIB32_ENV_ACTIVE=1

# lib32_stage_baseline
#   Record every path the package DESTDIR already holds, immediately before
#   this build stages anything into it. lib32_assert_only_lib32 subtracts this
#   record so it judges THIS build's staged payload rather than whatever the
#   staging root happens to contain.
#
#   Why it is needed: DESTDIR is per-package only on the tracked build path
#   (igos-build/builder.py creates and empties one staging dir per package). A
#   --stage-only build points DESTDIR at the SHARED system root instead, so the
#   root legitimately holds other packages' payloads and this package's own
#   previous run — including the license directory the bundle-license phase
#   writes after do_install. Measured 2026-09-19: a second --stage-only build of
#   lib32-libogg refused on
#   <staging>/usr/share/licenses/lib32-libogg/COPYING, written by its own
#   previous build. The assertion's contract is the staged PAYLOAD; its
#   population was the whole root.
#
#   The record is a temp FILE (a shared system root holds far too many paths for
#   a shell variable) and it is keyed to the DESTDIR it was taken against: the
#   bash drivers run every package in ONE shell, so a record that outlived its
#   package must never narrow another package's judgement. The assertion clears
#   it, and lib32_env_end clears it on the success path.
lib32_stage_baseline() {
    if [ -z "${DESTDIR:-}" ]; then
        echo "FATAL: lib32_stage_baseline: DESTDIR is unset — refusing to record a staging baseline" >&2
        return 1
    fi
    lib32_stage_baseline_clear
    IGOS_LIB32_STAGE_BASELINE=$(mktemp "${TMPDIR:-/tmp}/igos-lib32-baseline.XXXXXXXX") || {
        echo "FATAL: lib32_stage_baseline: could not create the baseline record" >&2
        return 1
    }
    IGOS_LIB32_STAGE_BASELINE_ROOT="${DESTDIR}"
    # A missing DESTDIR is not an error here: a fresh per-package staging root
    # that does not exist yet simply held nothing, and the record is empty.
    find "${DESTDIR}" -mindepth 1 2>/dev/null | LC_ALL=C sort > "${IGOS_LIB32_STAGE_BASELINE}"
}

# lib32_stage_baseline_clear
#   Remove the record and forget it. Safe to call when none was taken.
lib32_stage_baseline_clear() {
    if [ -n "${IGOS_LIB32_STAGE_BASELINE:-}" ]; then
        rm -f "${IGOS_LIB32_STAGE_BASELINE}"
    fi
    unset IGOS_LIB32_STAGE_BASELINE IGOS_LIB32_STAGE_BASELINE_ROOT
}

# lib32_stage_libs <private-install-root> [extra-relative-path ...]
#   Allowlist-stages ONLY the usr/lib32 tree from a private install root
#   into the package DESTDIR (D-W0-6: allowlist over denylist). Headers,
#   binaries, man pages, docs are deliberately NOT staged — they ship with
#   the 64-bit sibling; a lib32 package is runtime libs + its .pc files.
#   Optional extra RELATIVE paths (e.g. usr/share/vulkan/icd.d for the
#   32-bit ICD manifests a lib32 mesa must ship) are staged verbatim —
#   each extra MUST also be passed to lib32_assert_only_lib32, and the
#   recipe documents WHY it ships (an undeclared extra fails the assert).
lib32_stage_libs() {
    local root="$1"; shift
    if [ ! -d "${root}/usr/lib32" ]; then
        echo "FATAL: lib32_stage_libs: no usr/lib32 tree under ${root}" >&2
        return 1
    fi
    # BEFORE the first write into DESTDIR: everything already there belongs to
    # the staging root, not to this build.
    lib32_stage_baseline || return 1
    install -dm755 "${DESTDIR}/usr/lib32"
    cp -a "${root}/usr/lib32/." "${DESTDIR}/usr/lib32/"
    local extra
    for extra in "$@"; do
        if [ ! -e "${root}/${extra}" ] && [ ! -L "${root}/${extra}" ]; then
            echo "FATAL: lib32_stage_libs: declared extra '${extra}' not produced under ${root}" >&2
            return 1
        fi
        install -dm755 "${DESTDIR}/$(dirname "${extra}")"
        cp -a "${root}/${extra}" "${DESTDIR}/${extra}"
    done
}

# lib32_only_new_paths <baseline-file>
#   Filter of the two sweeps below: drop every path the staging root already
#   held when this build began staging. With no record, or an empty one (a
#   fresh per-package staging root held nothing), every path is this build's
#   and passes through. grep exits 1 when NOTHING survives the filter, which is
#   the clean case, not an error — only a real grep failure (exit 2) propagates.
lib32_only_new_paths() {
    local baseline="$1"
    if [ -n "${baseline}" ] && [ -s "${baseline}" ]; then
        grep -Fxv -f "${baseline}" || [ $? -eq 1 ]
    else
        cat
    fi
}

# lib32_assert_only_lib32 [extra-relative-path ...]
#   T3-class fail-loud assertion: the staged package payload must contain
#   NOTHING outside /usr/lib32 (plus any explicitly-declared extras, which
#   must match what lib32_stage_libs was given). Matches every
#   NON-DIRECTORY entry — regular files, symlinks, FIFOs, device nodes —
#   not just regular files (Wave-1 adversarial-verify finding W1-b: a
#   `-type f` filter let a planted stray SYMLINK ship unflagged; the guard
#   now allowlists directories only).
lib32_assert_only_lib32() {
    # pkg_stage pre-creates a merged-usr SKELETON in EVERY staging root:
    # bin/lib/sbin -> usr/* symlinks plus empty lib64 + usr/{bin,lib,sbin}
    # dirs. Verify-then-exclude (launch-gate L6 shape, proven in
    # lib32-glibc's recipe gate dev 3e639417; HOISTED here 2026-07-03 when
    # lib32-zlib — the first wave-2 twin through this shared gate — hit
    # the same wall): each skeleton symlink is asserted to be EXACTLY the
    # framework artifact (readlink == usr/<name>, else FATAL), and only
    # then excluded from the stray sweep. A real payload file named
    # bin/lib/sbin still refuses; CONTENT inside the skeleton dirs is
    # outside usr/lib32 and still refuses via this same sweep.
    # WHICH PATHS ARE THIS BUILD'S: the record lib32_stage_libs took before it
    # staged anything. A record that belongs to a different staging root is a
    # leak across packages in the drivers' shared shell — refuse rather than
    # judge this package against another package's paths. No record at all
    # means no narrowing: every path in the root is judged, which is the
    # pre-2026-09-20 behaviour and can only refuse MORE, never less.
    local baseline=""
    if [ -n "${IGOS_LIB32_STAGE_BASELINE:-}" ]; then
        if [ "${IGOS_LIB32_STAGE_BASELINE_ROOT:-}" != "${DESTDIR}" ]; then
            echo "FATAL: lib32_assert_only_lib32: the staging baseline was recorded for '${IGOS_LIB32_STAGE_BASELINE_ROOT:-}', not for DESTDIR '${DESTDIR}' — refusing" >&2
            lib32_stage_baseline_clear
            return 1
        fi
        baseline="${IGOS_LIB32_STAGE_BASELINE}"
    fi
    local skel
    for skel in bin lib sbin; do
        if [ -e "${DESTDIR}/${skel}" ] || [ -L "${DESTDIR}/${skel}" ]; then
            if [ ! -L "${DESTDIR}/${skel}" ] || \
               [ "$(readlink "${DESTDIR}/${skel}")" != "usr/${skel}" ]; then
                echo "FATAL: lib32 staging root '${skel}' is not the merged-usr skeleton symlink to usr/${skel} — refusing" >&2
                return 1
            fi
        fi
    done
    local find_args=("${DESTDIR}" '!' -type d '!' -path "${DESTDIR}/usr/lib32/*"
                     '!' -path "${DESTDIR}/bin"
                     '!' -path "${DESTDIR}/lib"
                     '!' -path "${DESTDIR}/sbin")
    local extra
    for extra in "$@"; do
        find_args+=('!' -path "${DESTDIR}/${extra}" '!' -path "${DESTDIR}/${extra}/*")
    done
    # The filter runs BEFORE head: five pre-existing paths would otherwise hide
    # a real stray behind them.
    local stray
    stray=$(find "${find_args[@]}" | lib32_only_new_paths "${baseline}" | head -5)
    if [ -n "$stray" ]; then
        echo "FATAL: lib32 package staged non-directory content outside the allowlist:" >&2
        echo "$stray" >&2
        lib32_stage_baseline_clear
        return 1
    fi
    # Stray EMPTY directories outside the allowlist halt too (the Wave-1
    # re-cert's flagged edge, closed 2026-07-02): a bare directory carries
    # no payload, but shipping one is recipe noise at best and a
    # permissions-bearing artifact at worst — the guard's contract is
    # "nothing outside the allowlist", directories included. Non-empty
    # stray dirs are already caught via their contents; this closes the
    # empty-dir remainder. Parent dirs of allowlisted content are
    # non-empty by construction and never match.
    # The four EMPTY skeleton dirs pkg_stage pre-creates (lib64 +
    # usr/{bin,lib,sbin}) are framework artifacts, tolerated ONLY while
    # empty — any content in them sits outside usr/lib32 and is refused by
    # the non-directory sweep above, so no payload can hide behind these
    # exclusions.
    local straydir_args=("${DESTDIR}" -mindepth 1 -type d -empty
                         '!' -path "${DESTDIR}/usr/lib32"
                         '!' -path "${DESTDIR}/usr/lib32/*"
                         '!' -path "${DESTDIR}/lib64"
                         '!' -path "${DESTDIR}/usr/bin"
                         '!' -path "${DESTDIR}/usr/lib"
                         '!' -path "${DESTDIR}/usr/sbin")
    for extra in "$@"; do
        straydir_args+=('!' -path "${DESTDIR}/${extra}" '!' -path "${DESTDIR}/${extra}/*")
    done
    stray=$(find "${straydir_args[@]}" | lib32_only_new_paths "${baseline}" | head -5)
    if [ -n "$stray" ]; then
        echo "FATAL: lib32 package staged stray EMPTY director(ies) outside the allowlist:" >&2
        echo "$stray" >&2
        lib32_stage_baseline_clear
        return 1
    fi
    # One staging pass, one judgement: the record does not outlive the
    # assertion that consumed it.
    lib32_stage_baseline_clear
}

# lib32_env_end
#   MANDATORY at the end of every lib32 recipe's do_install(): the bash
#   drivers run all packages in ONE shell, so the -m32 exports above would
#   leak into the next (64-bit) package (build-rules §2.2 env-leak class —
#   here WE are the leak source, so the profile cleans up after itself;
#   the RT-1 width audit is the backstop, never the plan). The chroot
#   baseline CFLAGS are untouched by this profile, so nothing to restore.
#   This is the SUCCESS-path cleanup; the failure path is covered by the
#   driver-side marker-keyed lib32_env_scrub (W1-a — see the marker above).
lib32_env_end() {
    unset CC CXX PKG_CONFIG_LIBDIR LIB32_HOST IGOS_LIB32_ENV_ACTIVE
    lib32_stage_baseline_clear
    if [ "${IGOS_LIB32_PREV_MAKEFLAGS-}" = "__unset__" ]; then
        unset MAKEFLAGS
    else
        MAKEFLAGS="${IGOS_LIB32_PREV_MAKEFLAGS}"; export MAKEFLAGS
    fi
    unset IGOS_LIB32_PREV_MAKEFLAGS
}
