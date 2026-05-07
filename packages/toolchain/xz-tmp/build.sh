#!/bin/bash
# xz 5.8.2 (temporary tools)
# LFS 13.0 Section 6.16

configure() {
    set -e
    ./configure --prefix=/usr                      \
                --host=$IGOS_TARGET                \
                --build=$(build-aux/config.guess)   \
                --disable-static                   \
                --docdir=/usr/share/doc/xz-${PKG_VERSION}
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

install() {
    set -e
    make DESTDIR=$IGOS install
    # LFS: remove libtool archive — harmful for cross-compilation
    rm -v $IGOS/usr/lib/liblzma.la
}
