#!/bin/bash
# Libpipeline 1.5.8
# LFS 13.0 Section 8.70

configure() {
    ./configure --prefix=/usr
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    # LFS: tests require the Check library which is not in LFS
    make check || true
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
