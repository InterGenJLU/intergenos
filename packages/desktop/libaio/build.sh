#!/bin/bash
# libaio 0.3.113 — Linux-native asynchronous I/O facility
# BLFS 13.0

configure() {
    # Skip static library install
    sed -i '/install.*libaio.a/s/^/#/' src/Makefile
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
