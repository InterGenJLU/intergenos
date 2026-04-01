#!/bin/bash
# Binutils 2.46.0 — Pass 2
# LFS 13.0 Section 6.17
#
# Cross-compiled binutils for the target system.

configure() {
    # Fix libtool issue that could link against host libraries
    sed '6031s/$add_dir//' -i ltmain.sh

    mkdir -v build
    cd       build

    ../configure                              \
        --prefix=/usr                         \
        --build=$(../config.guess)            \
        --host=$IGOS_TARGET                   \
        --disable-nls                         \
        --enable-shared                       \
        --enable-gprofng=no                   \
        --disable-werror                      \
        --enable-64-bit-bfd                   \
        --enable-new-dtags                    \
        --enable-default-hash-style=gnu
}

build() {
    cd build
    make -j${IGOS_JOBS}
}

install() {
    cd build
    make DESTDIR=$IGOS install

    # Remove libtool archives and unnecessary static libraries
    rm -v $IGOS/usr/lib/lib{bfd,ctf,ctf-nobfd,opcodes,sframe}.{a,la}
}
