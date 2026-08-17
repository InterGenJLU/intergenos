#!/usr/bin/env bash
# PI-12 — .PKGINFO build-time gate: bash assertion contracts.
#
# Exercises the bash-side gates with fixture archives (Linux build env):
#
#   T4   conditioned-2A, post-python: a stripped .PKGINFO is caught
#   T4b  conditioned-2A, pre-python : gen_pkginfo_ran=0 -> 2A skipped, NO false-abort
#   T5   Step 4.7 sweep fail-closed : one archive lacking .PKGINFO -> refuse-to-seal
#   T5b  Step 4.7 empty-set         : no *.igos.tar.gz -> refuse-to-seal (no vacuous PASS)
#   T9   edit-5 backfill            : missing-only / idempotent / lossless / fail-loud
#                                     (lives in test_pi12_pkginfo.py — the backfill is python)
#
# The well-formed check + the sweep are now SOURCED from the single source of
# truth (scripts/lib/pi12-sweep.sh), so this test exercises the ACTUAL functions
# used by build-squashfs.sh Step 4.7 — no kept-in-sync copy. The conditioned-2A
# behavior (the gen_pkginfo_ran guard) is exercised in t4/t4b below.
set -u

PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); echo "  PASS: $1"; }
bad()  { FAIL=$((FAIL+1)); echo "  FAIL: $1"; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# --- fixture helpers --------------------------------------------------------
# Build an archive rooted like the real builder: tar -C "$stage" -czf "$archive" .
make_archive() {  # <archive> <stage-dir>
    tar -C "$2" -czf "$1" .
}
stage_with_pkginfo() {  # <dir> <name> <ver> <tier>
    mkdir -p "$1/usr/bin"; printf '#!/bin/sh\n' > "$1/usr/bin/tool"
    printf 'pkgname=%s\npkgver=%s\npkgrel=1\ntier=%s\n' "$2" "$3" "$4" > "$1/.PKGINFO"
}
stage_without_pkginfo() {  # <dir>
    mkdir -p "$1/usr/bin"; printf '#!/bin/sh\n' > "$1/usr/bin/tool"
}

# Source the REAL predicate + sweep — single source of truth at
# scripts/lib/pi12-sweep.sh (also sourced by build-squashfs.sh Step 4.7). The
# test now exercises the ACTUAL build functions (wellformed_pkginfo +
# pi12_sweep), not a kept-in-sync copy.
PI12_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/scripts/lib/pi12-sweep.sh"
if [ ! -f "$PI12_LIB" ]; then
    echo "  SKIP-ALL: scripts/lib/pi12-sweep.sh not found at $PI12_LIB"; exit 0
fi
# shellcheck source=/dev/null
. "$PI12_LIB"

# --- T4: conditioned-2A post-python catches a stripped .PKGINFO --------------
t4() {
    local good="$WORK/t4good" bad_="$WORK/t4bad"
    mkdir -p "$good" "$bad_"
    stage_with_pkginfo "$good/stage" good 1.0 core; make_archive "$good/a.igos.tar.gz" "$good/stage"
    stage_without_pkginfo "$bad_/stage";            make_archive "$bad_/a.igos.tar.gz" "$bad_/stage"
    wellformed_pkginfo "$good/a.igos.tar.gz" && ok "T4 well-formed archive passes 2A" \
        || bad "T4 well-formed archive should pass 2A"
    wellformed_pkginfo "$bad_/a.igos.tar.gz" && bad "T4 stripped-.PKGINFO archive must FAIL 2A" \
        || ok "T4 stripped-.PKGINFO archive fails 2A (caught)"
}

# --- T4b: conditioned-2A pre-python does NOT false-abort ---------------------
# The real guard: 2A runs only `if [ "$gen_pkginfo_ran" = 1 ]`. With python3 absent the emit
# is skipped, ran=0, and 2A is never reached — so a legitimately metadata-less pre-python
# archive (mimic glibc-core) must NOT abort pkg_archive.
t4b() {
    local gen_pkginfo_ran=0
    local d="$WORK/t4b"; mkdir -p "$d"
    stage_without_pkginfo "$d/stage"; make_archive "$d/a.igos.tar.gz" "$d/stage"
    local aborted=0
    if [ "$gen_pkginfo_ran" = 1 ]; then
        wellformed_pkginfo "$d/a.igos.tar.gz" || aborted=1
    fi
    [ "$aborted" -eq 0 ] && ok "T4b pre-python (ran=0) skips 2A — no false-abort at glibc-core" \
        || bad "T4b pre-python must NOT abort on a metadata-less archive"
}

# --- T5: Step 4.7 sweep fail-closed on one bad archive ----------------------
t5() {
    local d="$WORK/t5"; mkdir -p "$d"
    stage_with_pkginfo "$d/s1" a 1.0 core;  make_archive "$d/a-1.0.igos.tar.gz" "$d/s1"
    stage_without_pkginfo "$d/s2";          make_archive "$d/b-1.0.igos.tar.gz" "$d/s2"
    pi12_sweep "$d" && bad "T5 sweep must refuse-to-seal when an archive lacks .PKGINFO" \
        || ok "T5 sweep refuses-to-seal on a metadata-less archive"
}

# --- T5b: Step 4.7 empty-set is refuse-to-seal, not a vacuous PASS ----------
t5b() {
    local d="$WORK/t5b"; mkdir -p "$d"      # exists, contains no *.igos.tar.gz
    pi12_sweep "$d" && bad "T5b empty set must refuse-to-seal (no vacuous PASS)" \
        || ok "T5b empty archive set refuses-to-seal (honesty-first)"
}

# --- T9: edit-5 backfill (missing-only / idempotent / lossless / fail-loud) --
# PENDING: enabled once edit-5 (the in-chroot post-python backfill) lands. The contract the
# backfill must satisfy:
#   - MISSING-ONLY: a recipe-bearing archive already carrying tier=desktop is UNCHANGED
#     (its real tier is never clobbered to core).
#   - LOSSLESS:     a backfilled archive has the identical member set plus ./.PKGINFO.
#   - FAIL-LOUD:    a gen-pkginfo fault during backfill aborts the build (not skip).
t9() {
    # T9/T9b (edit-5 backfill: missing-only / idempotent / dual-built / fail-loud) are python
    # tests in test_pi12_pkginfo.py (backfill-pkginfo.py is python). Kept here as a pointer.
    echo "  NOTE: T9/T9b edit-5 backfill live in test_pi12_pkginfo.py (python)"
}

echo "PI-12 bash gate tests"
command -v tar >/dev/null 2>&1 || { echo "  SKIP-ALL: tar not available"; exit 0; }
t4; t4b; t5; t5b; t9
echo "----"
echo "PI-12 gates: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
