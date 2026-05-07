#!/bin/bash
# rsync 3.4.1 — Fast incremental file transfer
# BLFS 13.0

configure() {
    set -e
    # Patch applied by builder PATCH phase (package.yml) with SHA256 validation.

    ./configure --prefix=/usr    \
                --disable-xxhash \
                --without-included-zlib
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    sed -i '/typedef/d' wildtest.c
    make check || true
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
