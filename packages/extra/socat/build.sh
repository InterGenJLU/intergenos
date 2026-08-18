#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# socat 1.8.1.3 — connects any two data channels to each other: sockets, files,
# pipes, devices, TLS sessions, sub-processes. The tool people reach for when a
# connection has to be reshaped rather than merely made.
#
# Build facts verified against the pinned tarball:
#   - autotools with a generated ./configure.
#   - The install rule (Makefile.in:133-138) installs the binary as socat1 and
#     then creates the socat symlink to it, plus the socat-chain.sh and
#     socat-mux.sh helper scripts; the manual page is doc/socat.1. verify_paths
#     asserts /usr/bin/socat, the name users invoke, which the symlink provides.
#   - Readline support is checked at configure time and can fall back to either
#     curses or ncurses (configure.ac:608-632); both readline and ncurses are
#     declared so the choice is not left to what happens to be present.
#
# --enable-openssl is explicit. socat's TLS address types are the reason it is
# used for anything security-relevant, and leaving the backend to a configure
# probe would allow a build without them that still calls itself socat.

configure() {
    set -e
    ./configure               \
        --prefix=/usr         \
        --mandir=/usr/share/man \
        --enable-openssl      \
        --enable-readline
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        make test
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
