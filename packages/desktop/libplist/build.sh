#!/bin/bash
# libplist 2.7.0 — Apple property list library
# Not in BLFS — standard autotools

configure() {
    PYTHON=python3 \
    ./configure --prefix=/usr \
                --disable-static
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
