#!/bin/bash
# sdl2-ttf 2.24.0 — TrueType font rendering library for SDL2
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --disable-static
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
