#!/bin/bash
# setxkbmap 1.3.4 — Set the keyboard using the X Keyboard Extension
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
