#!/bin/bash
# gnutls 3.8.8 — GNU TLS library
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --disable-static \
                --with-default-trust-store-pkcs11=pkcs11:
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
