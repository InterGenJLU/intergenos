#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# p11-kit 0.26.2 — PKCS#11 module loading library
# BLFS 13.0

configure() {
    set -e
    mkdir -p p11-build
    cd    p11-build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -D trust_paths=/etc/pki/anchors
}

build() {
    set -e
    cd p11-build
    ninja
}

check() {
    set -e
    cd p11-build
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        ninja test
}

do_install() {
    set -e
    cd p11-build
    DESTDIR="$DESTDIR" ninja install

    install -Dm755 "$(dirname "${BASH_SOURCE[0]}")/files/usr/libexec/p11-kit/trust-extract-compat" \
        "${DESTDIR}/usr/libexec/p11-kit/trust-extract-compat"

    # Create update-ca-certificates symlink
    ln -sfv /usr/libexec/p11-kit/trust-extract-compat \
            "${DESTDIR}/usr/bin/update-ca-certificates"

    # Make p11-kit trust module available to NSS
    ln -sfv ./pkcs11/p11-kit-trust.so "${DESTDIR}/usr/lib/libnssckbi.so"
}
