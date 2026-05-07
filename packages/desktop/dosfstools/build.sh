#!/bin/bash
# dosfstools 4.2 — FAT filesystem utilities
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr   \
                --enable-compat-symlinks \
                --mandir=/usr/share/man \
                --docdir=/usr/share/doc/dosfstools-${version}
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
