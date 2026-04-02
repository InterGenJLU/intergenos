#!/bin/bash
# libxml2 2.13.5 — XML parsing library
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --disable-static \
                --with-history \
                --with-icu
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
