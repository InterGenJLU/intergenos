#!/bin/bash
# a52dec 0.8.0 — Library for decoding ATSC A/52 (AC-3 / Dolby Digital) streams
# Upstream: https://git.adelielinux.org/community/a52dec/
# BLFS 13.0 multimedia/liba52

configure() {
    set -e
    # BLFS: -fPIC is strictly required on x86_64 (runtime text relocation is
    # prohibited). Preserve upstream default optimisation (-g -O3) when CFLAGS
    # is unset, otherwise append -fPIC to whatever the build environment set.
    ./configure --prefix=/usr           \
                --mandir=/usr/share/man \
                --enable-shared         \
                --disable-static        \
                CFLAGS="${CFLAGS:--g -O3} -fPIC"
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    # BLFS: install internal header so consumers (xine-lib, etc.) can link
    # against the system-installed liba52.
    install -v -m644 liba52/a52_internal.h "$DESTDIR/usr/include/a52dec/"
}
