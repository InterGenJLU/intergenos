#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# cURL 8.19.0 — URL transfer library and tool
# BLFS 13.0

configure() {
    set -e
    # CA trust: configure BOTH the single-file bundle AND the hashed-
    # symlink directory. Our ca-certificates package ships the bundle at
    # /etc/ssl/certs/ca-certificates.crt AND populates the directory
    # with c_rehash symlinks. Configuring both gives curl a working
    # default whether callers pass `--cacert` or `--capath` (or neither).
    #
    # The path-only build (no --with-ca-bundle) was the 2026-05-25 live-
    # ISO bug: curl refused all HTTPS verifications until the rehash
    # symlinks existed in /etc/ssl/certs/, and our pre-2026-05-25
    # ca-certificates package shipped only the bundle file. The
    # vscode/chrome/edge helpers silently failed on a fresh live ISO
    # because their helper-lib curl calls hit verify errors.
    ./configure --prefix=/usr    \
                --disable-static \
                --with-openssl   \
                --with-libssh2   \
                --with-ca-bundle=/etc/ssl/certs/ca-certificates.crt \
                --with-ca-path=/etc/ssl/certs
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

    # Install documentation
    rm -rf docs/examples/.deps

    find docs \( -name Makefile\* -o  \
                 -name \*.1       -o  \
                 -name \*.3       -o  \
                 -name CMakeLists.txt \) -delete

    install -v -d -m755 "${DESTDIR}/usr/share/doc/curl-${PKG_VERSION}"
    cp -v -R docs/* "${DESTDIR}/usr/share/doc/curl-${PKG_VERSION}/"
}
