#!/bin/bash
# keyutils 1.6.3 — Linux key management utilities
# BLFS 13.0

build() {
    make LIBDIR=/usr/lib USRLIBDIR=/usr/lib BINDIR=/usr/bin SBINDIR=/usr/sbin -j${IGOS_JOBS}
}

do_install() {
    make LIBDIR=/usr/lib USRLIBDIR=/usr/lib BINDIR=/usr/bin SBINDIR=/usr/sbin DESTDIR="$DESTDIR" install
}
