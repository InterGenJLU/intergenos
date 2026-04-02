#!/bin/bash
# giflib 5.2.2 — GIF image library
# BLFS 13.0

build() {
    make  -j${IGOS_JOBS}
}

do_install() {
    make PREFIX=/usr DESTDIR="$DESTDIR" install
}
