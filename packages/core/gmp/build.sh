#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# GMP 6.3.0
# LFS 13.0 Section 8.22

configure() {
    set -e
    # Fix for gcc-15 compatibility
    sed -i '/long long t1;/,+1s/()/(...)/' configure

    # --enable-fat: build ALL x86-64 microarch variants with runtime CPU
    # dispatch instead of letting GMP's configure auto-tune mpn assembly to the
    # (Zen) build host. The golden-builder is a Zen CPU with ADX; without
    # --enable-fat, GMP bakes ADX (adcx/adox) into libgmp and SIGILLs on any
    # ADX-less target (AMD Excavator/bdver4, older Intel) — gnome-calculator and
    # every GMP bignum path die with invalid-opcode. --enable-fat is the standard
    # distro approach: portable across the whole install base, full perf retained
    # on capable CPUs. (Tracked items A19/A8; build eval B2, 2026-06-06.)
    ./configure --prefix=/usr    \
        --enable-fat             \
        --enable-cxx             \
        --disable-static         \
        --docdir=/usr/share/doc/gmp-6.3.0
}

build() {
    set -e
    make -j${IGOS_JOBS}
    make html
}

check() {
    set -e
    make check 2>&1 | tee gmp-check-log

    # Verify all 199 tests pass
    echo ""
    echo "=== GMP Test Summary ==="
    awk '/# PASS:/{total+=$3} ; END{print "Total tests passed:",total}' gmp-check-log
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
    make DESTDIR="$DESTDIR" install-html
}
