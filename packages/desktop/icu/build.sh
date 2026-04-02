#!/bin/bash
# icu 76-1 — International Components for Unicode
# BLFS 13.0
# Note: source tarball extracts to icu/ with source/ subdir

configure() {
    cd source &&
    ./configure --prefix=/usr
}

build() {
    cd source &&
    make -j${IGOS_JOBS}
}

check() {
    cd source &&
    make check || true
}

do_install() {
    cd source &&
    make DESTDIR="$DESTDIR" install
}
