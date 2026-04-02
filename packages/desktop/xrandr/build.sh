#!/bin/bash
# xrandr 1.5.3 — Primitive command line interface to RandR extension
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
