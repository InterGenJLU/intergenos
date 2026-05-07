#!/bin/bash
# libaio 0.3.113 — Linux-native asynchronous I/O facility
# BLFS 13.0

configure() {
    set -e
    # Skip static library install
    sed -i '/install.*libaio.a/s/^/#/' src/Makefile
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
