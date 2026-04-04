#!/bin/bash
# gnome-user-docs 49.4 — GNOME user documentation
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
