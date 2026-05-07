#!/bin/bash
# pinentry 1.3.2 — PIN/passphrase entry dialog
# BLFS 13.0

configure() {
    set -e
    # BLFS required fixes
    sed -i "/FLTK 1/s/3/4/" configure
    sed -i '14456 s/1.3/1.4/' configure
    ./configure --prefix=/usr \
                --enable-pinentry-tty
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
