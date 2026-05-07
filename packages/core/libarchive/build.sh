#!/bin/bash
# libarchive 3.8.6 — Multi-format archive library
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr --disable-static
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
    make DESTDIR="$DESTDIR" install
    # Create unzip symlink to bsdunzip
    ln -sfv bsdunzip "${DESTDIR}/usr/bin/unzip"
}
