#!/bin/bash
# libxcb 1.17.0 — X C Binding library
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --sysconfdir=/etc \
                --localstatedir=/var \
                --disable-static \
                --without-doxygen
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
