#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# whois 5.6.6 — RIPE-style WHOIS client. Mirrors BLFS 13.0.
# Source is Debian's byte-stable repack (content-verified identical to the upstream
# rfc1036 v5.6.6 tag); GitHub auto-archive tarballs are NOT checksum-stable, which is
# why we do not fetch from the GitHub /archive/ URL.

configure() {
    set -e
    :  # No separate configure step; the Makefile autodetects libidn2 via pkg-config.
}

build() {
    set -e
    # Builds the whois client (and mkpasswd, which we deliberately do NOT install).
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    # install-whois + the locale catalogs (install-pos). NOT install-mkpasswd:
    # 'expect' (core) already owns /usr/bin/mkpasswd, and whois's mkpasswd is a
    # different tool of the same name — two packages cannot own one path.
    make prefix=/usr BASEDIR="${DESTDIR}" install-whois install-pos
}
