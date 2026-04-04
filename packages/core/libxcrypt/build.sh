#!/bin/bash
# Libxcrypt 4.5.2
# LFS 13.0 Section 8.28

configure() {
    # Fix for glibc-2.43 compatibility
    sed -i '/strchr/s/const//' lib/crypt-{sm3,gost}-yescrypt.c

    ./configure --prefix=/usr                \
        --enable-hashes=strong,glibc         \
        --enable-obsolete-api=no             \
        --disable-static                     \
        --disable-failure-tokens
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make check || true
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
