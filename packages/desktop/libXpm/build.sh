#!/bin/bash
# libXpm 3.5.17 — X Pixmap library
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --disable-static \
                --disable-open-zfile
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
