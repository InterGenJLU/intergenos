#!/bin/bash
# giflib 5.2.2 — GIF image library
# BLFS 13.0

configure() {
    # Pre-place the logo so the Makefile doesn't need ImageMagick's convert
    cp pic/gifgrid.gif doc/giflib-logo.gif
}

build() {
    make  -j${IGOS_JOBS}
}

do_install() {
    make PREFIX=/usr DESTDIR="$DESTDIR" install
}
