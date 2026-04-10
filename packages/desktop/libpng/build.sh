#!/bin/bash
# libpng 1.6.55 — PNG reference library
# BLFS 13.0

configure() {
    # APNG patch applied by builder PATCH phase (package.yml) with SHA256 validation.

    ./configure --prefix=/usr \
                --disable-static
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
