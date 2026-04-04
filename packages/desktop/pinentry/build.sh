#!/bin/bash
# pinentry 1.3.2 — PIN/passphrase entry dialog
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed -i '14456 s/1.3/1.4/' configure
    ./configure --prefix=/usr \
                --enable-pinentry-tty
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
