#!/bin/bash
# mypaint-brushes 1.3.1 — Brush data for applications using libmypaint
# BLFS 13.0

configure() {
    ./configure --prefix=/usr
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
