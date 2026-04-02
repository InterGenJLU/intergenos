#!/bin/bash
# libarchive 3.8.6 — Multi-format archive library
# BLFS 13.0

configure() {
    ./configure --prefix=/usr --disable-static
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make check || true
}

do_install() {
    make DESTDIR="$DESTDIR" install
    # Create unzip symlink to bsdunzip
    ln -sfv bsdunzip "${DESTDIR}/usr/bin/unzip"
}
