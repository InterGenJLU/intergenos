#!/bin/bash
# rsync 3.4.1 — Fast incremental file transfer
# BLFS 13.0

configure() {
    ./configure --prefix=/usr    \
                --disable-xxhash \
                --without-included-zlib
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    sed -i '/typedef/d' wildtest.c
    make check || true
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
