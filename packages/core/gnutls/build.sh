#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# gnutls 3.8.12 — GNU TLS library
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr \
                --disable-static \
                --with-default-trust-store-pkcs11=pkcs11:
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
