#!/bin/bash
# libcanberra 0.30 — XDG sound theme and event sounds library
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --disable-static \
                --disable-oss
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
