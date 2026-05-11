#!/bin/bash
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
