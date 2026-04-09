#!/bin/bash
# ImageMagick 7.1.2-13 — Image processing and conversion suite
# BLFS 13.0

configure() {
    ./configure --prefix=/usr     \
                --sysconfdir=/etc \
                --enable-hdri     \
                --with-modules    \
                --with-perl=no    \
                --disable-static
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make check || true
}

do_install() {
    make DESTDIR="$DESTDIR" \
         DOCUMENTATION_PATH=/usr/share/doc/imagemagick-7.1.2 \
         install
}
