#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# nmap 7.991 — discovers which hosts are on a network, which services they
# expose, and what those services report about themselves.
#
# ⚠️ THE LOAD-BEARING DECISION IN THIS RECIPE IS WHICH LIBRARIES IT LINKS.
# The nmap tarball BUNDLES copies of libpcap, zlib, libssh2, liblua and libpcre
# (nmap-7.991/libpcap/, libz/, and so on), and its configure will happily build
# against those bundled copies. Every one of those is also a package in this
# tree. A bundled copy compiled into nmap is invisible to the package graph: it
# would not be rebuilt when the system's libpcap is updated for a security fix,
# and nothing would report that nmap was still carrying the old code. So each
# --with-*=/usr below names the system library explicitly, and the packages are
# declared as dependencies so the graph knows the edge exists. This is the same
# reasoning the tree applies to the xxhash Python binding, which is compiled
# against the system library rather than its vendored copy.
#
# Build facts verified against the pinned tarball:
#   - autotools with a generated ./configure; the options used here appear in
#     its --help output (--with-libpcap, --with-libssh2, --with-liblua,
#     --with-libpcre, --with-libssl, --with-libz), each accepting either a
#     directory or the literal "included". The option for OpenSSL is
#     --with-libssl, not --with-openssl; the wrong spelling would have been
#     accepted silently as an unrecognised option and the bundled default used.
#   - docs/nmap.1, ncat/docs/ncat.1 and nping/docs/nping.1 are the manual pages
#     the install rule places under man1.
#
# The graphical front end and the Python comparison tool are excluded:
# --without-zenmap drops a GTK application that has no place in a mirror-only
# command-line diagnostic package, and --without-ndiff drops a Python script
# whose dependency is unrelated to the scanner itself. Neither exclusion
# changes what the scanner can do; both keep the package's dependency surface
# to the libraries the scanning code actually links.
#
# UPSTREAM LICENCE, stated because it is not a common one: nmap ships under the
# Nmap Public Source Licence, which is derived from GPL-2.0 but adds terms —
# it is not OSI-approved and it restricts redistribution inside proprietary
# products. The SPDX identifier recorded in package.yml names it accurately
# rather than approximating it as GPL-2.0.

configure() {
    set -e
    ./configure                        \
        --prefix=/usr                  \
        --mandir=/usr/share/man        \
        --with-libpcap=/usr            \
        --with-libssh2=/usr            \
        --with-liblua=/usr             \
        --with-libpcre=/usr            \
        --with-libz=/usr               \
        --with-libssl=/usr             \
        --without-zenmap               \
        --without-ndiff
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
