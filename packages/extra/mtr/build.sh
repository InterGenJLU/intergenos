#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# mtr 0.96 — sends a continuous sequence of probes along a route and reports
# per-hop loss and latency, which is what makes it the tool that finds WHERE a
# path is failing rather than only that it is.
#
# Build facts verified against the pinned tarball:
#   - autotools, and the GitHub tag archive ships configure.ac with NO generated
#     ./configure, so bootstrap.sh/autoreconf runs first — hence the autoconf,
#     automake and libtool build dependencies that the release-tarball packages
#     in this wave do not need.
#   - Makefile.am installs sbin_PROGRAMS = mtr mtr-packet and
#     dist_man_MANS = mtr.8 mtr-packet.8.
#   - The privileged work is isolated in the separate mtr-packet helper, and
#     libcap is checked unconditionally (configure.ac:145) because that split is
#     how mtr avoids running its whole user interface with raw-socket privilege.
#   - --without-gtk is passed deliberately: configure looks for gtk+-3.0 and
#     builds an additional GTK front end if it finds it (configure.ac:97). This
#     package is a mirror-only command-line diagnostic, and letting a GUI appear
#     in it depending on whether GTK happened to be in the chroot would make the
#     package's contents depend on build-time state. The curses interface, which
#     is the one this package is for, is unaffected.

configure() {
    set -e
    ./bootstrap.sh
    ./configure              \
        --prefix=/usr        \
        --sbindir=/usr/sbin  \
        --mandir=/usr/share/man \
        --without-gtk
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
