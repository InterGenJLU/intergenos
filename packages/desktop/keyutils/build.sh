#!/bin/bash
# keyutils 1.6.3 — Linux key management utilities
# BLFS 13.0

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make NO_ARLIB=1 \
         LIBDIR=/usr/lib \
         BINDIR=/usr/bin \
         SBINDIR=/usr/sbin \
         DESTDIR="$DESTDIR" install
}
