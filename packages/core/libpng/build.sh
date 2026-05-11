#!/bin/bash
# libpng 1.6.55 — PNG reference library
# BLFS 13.0

configure() {
    set -e
    # APNG patch applied by builder PATCH phase (package.yml) with SHA256 validation.

    ./configure --prefix=/usr \
                --disable-static
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
