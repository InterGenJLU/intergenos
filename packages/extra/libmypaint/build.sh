#!/bin/bash
# libmypaint 1.6.1 — Brush engine library for MyPaint and GIMP
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
