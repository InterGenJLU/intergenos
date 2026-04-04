#!/bin/bash
# libxslt 1.1.45 — XSLT processor library
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --disable-static \
                --without-python
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
