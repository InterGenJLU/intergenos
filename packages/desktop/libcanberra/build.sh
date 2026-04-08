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
    # Remove .la files before install to prevent libtool relink failures.
    # Libtool relink chokes on GCC 15 when relinking modules during
    # DESTDIR install (file format not recognized on valid .so files).
    find . -name "*.la" -delete

    make DESTDIR="$DESTDIR" install
}
