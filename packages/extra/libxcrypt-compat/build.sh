#!/bin/bash
# libxcrypt-compat 4.5.2 — LSB ABI 1 compatibility library
# LFS 13.0 Section 8.28 (optional instructions)
#
# Rebuilds libxcrypt with --enable-obsolete-api=glibc to produce
# libcrypt.so.1 alongside the existing libcrypt.so.2.

configure() {
    # Same glibc-2.43 fix as the core build
    sed -i '/strchr/s/const//' lib/crypt-{sm3,gost}-yescrypt.c

    ./configure --prefix=/usr                \
        --enable-hashes=strong,glibc         \
        --enable-obsolete-api=glibc          \
        --disable-static                     \
        --disable-failure-tokens
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    # Only install the ABI 1 compat library — do NOT overwrite libcrypt.so.2
    cp -av --remove-destination .libs/libcrypt.so.1* "$DESTDIR/usr/lib/"
}
