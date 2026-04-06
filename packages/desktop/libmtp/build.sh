#!/bin/bash
# libmtp 1.1.23 — MTP media device access library
# Not in BLFS — standard autotools

configure() {
    ./configure --prefix=/usr       \
                --disable-static    \
                --with-udev=/usr/lib/udev
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
