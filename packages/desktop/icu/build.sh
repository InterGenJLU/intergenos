#!/bin/bash
# icu 76-1 — International Components for Unicode
# BLFS 13.0
# Note: source tarball extracts to icu/ with source/ subdir

configure() {
    set -e
    cd source &&
    ./configure --prefix=/usr
}

build() {
    set -e
    cd source &&
    make -j${IGOS_JOBS}
}

check() {
    set -e
    cd source &&
    make check || true
}

do_install() {
    set -e
    cd source &&
    make DESTDIR="$DESTDIR" install
}
