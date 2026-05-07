#!/bin/bash
# Lz4 1.10.0
# LFS 13.0 Section 8.9
#
# DESTDIR exception: needs both PREFIX and DESTDIR.

configure() {
    set -e
    : # Plain Makefile, no configure
}

build() {
    set -e
    make BUILD_STATIC=no PREFIX=/usr -j${IGOS_JOBS}
}

check() {
    set -e
    make -j1 check
}

do_install() {
    set -e
    make BUILD_STATIC=no PREFIX=/usr DESTDIR="$DESTDIR" install
}
