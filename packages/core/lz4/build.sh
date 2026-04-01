#!/bin/bash
# Lz4 1.10.0
# LFS 13.0 Section 8.9
#
# DESTDIR exception: needs both PREFIX and DESTDIR.

configure() {
    : # Plain Makefile, no configure
}

build() {
    make BUILD_STATIC=no PREFIX=/usr -j${IGOS_JOBS}
}

check() {
    make -j1 check
}

install() {
    make BUILD_STATIC=no PREFIX=/usr DESTDIR="$DESTDIR" install
}
