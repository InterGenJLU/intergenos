# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
# pi12-sweep.sh — the canonical PI-12 .PKGINFO predicate + pre-squashfs sweep.
#
# Single source of truth, sourced by:
#   - scripts/build-squashfs.sh Step 4.7  (the real pre-seal refuse-to-seal gate)
#   - tests/pi12/test_pi12_gates.sh        (T4/T4b/T5/T5b call the REAL functions,
#                                           not a kept-in-sync copy)
#
# Functions are pure (rc-returning); pi12_sweep prints offending archive names +
# a one-line summary to stdout so callers can route them through their own logger.
# This file is meant to be SOURCED — it defines functions and runs nothing.

# wellformed_pkginfo <archive> -> rc 0 if the archive carries a ./.PKGINFO with
# at least pkgname + pkgver + pkgrel (the 2A + Step 4.7 predicate).
wellformed_pkginfo() {
    local info
    info="$(tar -xzOf "$1" ./.PKGINFO 2>/dev/null)" || info=""
    printf '%s\n' "$info" | grep -qE '^pkgname=' \
        && printf '%s\n' "$info" | grep -qE '^pkgver=' \
        && printf '%s\n' "$info" | grep -qE '^pkgrel='
}

# pi12_sweep <archive-dir> -> rc 0 PASS / 1 refuse-to-seal.
# An EMPTY set is refuse-to-seal (honesty-first: never a vacuous PASS). Prints
# the offending archive names + a one-line verdict to stdout.
pi12_sweep() {
    local dir="$1" missing=0 found=0 a
    for a in "$dir"/*.igos.tar.gz; do
        [ -e "$a" ] || continue
        found=$((found + 1))
        if ! wellformed_pkginfo "$a"; then
            echo "MISSING/malformed .PKGINFO: ${a##*/}"
            missing=$((missing + 1))
        fi
    done
    if [ "$found" -eq 0 ]; then
        echo "no *.igos.tar.gz staged in $dir"
        return 1
    fi
    if [ "$missing" -ne 0 ]; then
        echo "$missing of $found archive(s) lack a well-formed .PKGINFO"
        return 1
    fi
    echo "all $found staged archive(s) carry pkgname/pkgver/pkgrel"
    return 0
}
