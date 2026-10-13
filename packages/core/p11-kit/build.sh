#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# p11-kit 0.26.2 — PKCS#11 module loading library
# BLFS 13.0

configure() {
    set -e
    # The 2026-10-12 resume found an optional libnsl installed later in the
    # build. Prefer libc's gethostbyname, as the upstream Autoconf path does,
    # so Meson keeps the original links without moving later dependencies.
    # The fallback still probes libnsl on platforms where libc lacks it.
    patch --batch --forward -p1 < \
        "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/patches/0001-prefer-libc-for-network-functions.patch"
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
