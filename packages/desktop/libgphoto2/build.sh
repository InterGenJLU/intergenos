#!/bin/bash
# libgphoto2 2.5.33 — Digital camera access library
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

post_install() {
    set -e
    udevadm control --reload 2>/dev/null || true
    udevadm trigger 2>/dev/null || true
}
