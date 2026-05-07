#!/bin/bash
# libusbmuxd 2.1.1 — USB multiplexing daemon client library
# Not in BLFS — standard autotools

configure() {
    set -e
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
