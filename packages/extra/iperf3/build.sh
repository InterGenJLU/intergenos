#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# iperf3 3.21 — measures how much bandwidth a path between two hosts actually
# delivers, which is the measurement that separates "the network is slow" from
# "this application is slow".
#
# Build facts verified against the pinned tarball:
#   - autotools with a generated ./configure.
#   - src/Makefile.am installs bin_PROGRAMS = iperf3 and
#     dist_man_MANS = iperf3.1 libiperf.3, plus the libiperf shared library the
#     binary links against — hence libiperf.so in verify_paths, which would
#     catch a build that produced the tool without its library.
#   - OpenSSL is used for the authenticated-session feature; it is a core
#     package here, so the feature is built rather than silently absent.
#
# Note on naming: upstream's project and tarball are called "iperf", and the
# binary is "iperf3". The package takes the binary's name because that is what
# a user types and what other tools reference, and because a future iperf4
# would otherwise collide with it.

configure() {
    set -e
    ./configure               \
        --prefix=/usr         \
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
