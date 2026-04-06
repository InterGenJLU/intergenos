#!/bin/bash
# libimobiledevice 1.4.0 — Apple mobile device access library
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

post_install() {
    udevadm control --reload 2>/dev/null || true
    udevadm trigger 2>/dev/null || true
}
