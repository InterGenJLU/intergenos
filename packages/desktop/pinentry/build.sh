#!/bin/bash
# pinentry 1.3.1 — PIN/passphrase entry dialog
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --enable-pinentry-tty
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
