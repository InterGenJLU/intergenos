#!/bin/bash
# gawk 5.3.2 (temporary tools)
# LFS 13.0 Section 6.9

configure() {
    # LFS: prevent installation of unnecessary extras
    sed -i 's/extras//' Makefile.in

    ./configure --prefix=/usr                      \
                --host=$IGOS_TARGET                \
                --build=$(build-aux/config.guess)
}

build() {
    make -j${IGOS_JOBS}
}

install() {
    make DESTDIR=$IGOS install
}
