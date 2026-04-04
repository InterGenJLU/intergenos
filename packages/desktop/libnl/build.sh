#!/bin/bash
# libnl 3.12.0 — Netlink protocol library suite
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --sysconfdir=/etc \
                --disable-static
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
