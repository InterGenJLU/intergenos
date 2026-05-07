#!/bin/bash
# libimobiledevice 1.4.0 — Apple mobile device access library
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
