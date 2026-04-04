#!/bin/bash
# libXdmcp 1.1.5 — X Display Manager Control Protocol library
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --sysconfdir=/etc \
                --localstatedir=/var \
                --disable-static
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
