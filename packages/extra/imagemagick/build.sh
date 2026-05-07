#!/bin/bash
# ImageMagick 7.1.2-13 — Image processing and conversion suite
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr     \
                --sysconfdir=/etc \
                --enable-hdri     \
                --with-modules    \
                --with-perl=no    \
                --disable-static
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    make check || true
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" \
         DOCUMENTATION_PATH=/usr/share/doc/imagemagick-7.1.2 \
         install
}
