#!/bin/bash
# libpwquality 1.4.5 — Password quality checking library
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --disable-static \
                --with-securedir=/usr/lib/security
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
