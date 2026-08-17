#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# nodejs 22.22.0 — JavaScript runtime built on V8
# BLFS 13.0

configure() {
    set -e
    # Patch applied by builder PATCH phase (package.yml) with SHA256 validation.

    # Use system libraries instead of bundled copies
    ./configure --prefix=/usr          \
                --shared-brotli        \
                --shared-cares         \
                --shared-libuv         \
                --shared-openssl       \
                --shared-nghttp2       \
                --shared-zlib          \
                --with-intl=system-icu
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    # ~10 of 4600+ tests known to fail per BLFS
    make test-only || true
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    # Doc symlink ships as owned payload (hook-contract wave).
    install -dm755 "${DESTDIR}/usr/share/doc"
    ln -sfn node "${DESTDIR}/usr/share/doc/node-${PKG_VERSION}"
}

