#!/bin/bash
# xz 5.8.2 (temporary tools)
# LFS 13.0 Section 6.16

configure() {
    ./configure --prefix=/usr                      \
                --host=$IGOS_TARGET                \
                --build=$(build-aux/config.guess)   \
                --disable-static                   \
                --docdir=/usr/share/doc/xz-${PKG_VERSION}
}

build() {
    make -j${IGOS_JOBS}
}

install() {
    make DESTDIR=$IGOS install
    # LFS: remove libtool archive — harmful for cross-compilation
    rm -v $IGOS/usr/lib/liblzma.la
}
