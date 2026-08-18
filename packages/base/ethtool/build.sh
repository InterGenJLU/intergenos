#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# ethtool 7.1 — read and change network interface driver and hardware settings
# (link state, ring sizes, offloads, driver identity, per-queue statistics).
#
# Build system verified against the pinned tarball:
#   - autotools with a pre-generated ./configure.
#   - Exactly one library dependency: PKG_CHECK_MODULES([MNL],[libmnl]) in
#     configure.ac. libmnl is a core package, so this base-tier recipe reaches
#     only backwards in the tier order.
#   - Makefile.am installs sbin_PROGRAMS = ethtool and man_MANS = ethtool.8,
#     so both the binary and its manual page come from upstream's own install
#     rule.

configure() {
    set -e
    ./configure            \
        --prefix=/usr      \
        --sbindir=/usr/sbin \
        --mandir=/usr/share/man
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        make check
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
