#!/bin/bash
# Libpipeline 1.5.8
# LFS 13.0 Section 8.70

configure() {
    set -e
    ./configure --prefix=/usr
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    # LFS: tests require the Check library which is not in LFS
    make check || true
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
