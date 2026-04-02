#!/bin/bash
# xcb-util-keysyms 0.4.1 — XCB keysym convenience library
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
