#!/bin/bash
# libgphoto2 2.5.33 — Digital camera access library
# Not in BLFS — standard autotools

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
