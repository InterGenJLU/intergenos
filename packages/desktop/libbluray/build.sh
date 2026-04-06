#!/bin/bash
# libbluray 1.4.1 — Blu-ray disc playback library
# Not in BLFS — standard autotools

configure() {
    ./configure --prefix=/usr          \
                --disable-static       \
                --disable-bdjava-jar
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
