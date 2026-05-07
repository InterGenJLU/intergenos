#!/bin/bash
# libplist 2.7.0 — Apple property list library
# Not in BLFS — standard autotools

configure() {
    set -e
    PYTHON=python3 \
    ./configure --prefix=/usr \
                --disable-static
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
