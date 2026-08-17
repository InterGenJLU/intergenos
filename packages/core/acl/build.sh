#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Acl 2.3.2
# LFS 13.0 Section 8.26

configure() {
    set -e
    ./configure --prefix=/usr    \
        --disable-static         \
        --docdir=/usr/share/doc/acl-2.3.2
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    # LFS 13.0 §8.26: exactly ONE test is known to fail here — test/cp.test —
    # "because Coreutils is not built with the Acl support yet." Validate that
    # this is the ONLY failure (an allow-list, not a blanket non-fatal): any other
    # failure is a real regression and HALTS the build via the helper's exit 1.
    local _testlog _rc=0
    _testlog="$(mktemp)"
    make check > "$_testlog" 2>&1 || _rc=$?
    cat "$_testlog"   # preserve full test output to the per-package log
    pkg_assert_known_test_failures "$_rc" "$_testlog" "test/cp.test"
    rm -f "$_testlog"
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
