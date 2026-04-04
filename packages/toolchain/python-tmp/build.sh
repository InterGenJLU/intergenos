#!/bin/bash
# Python 3.14.3 (temporary tools)
# LFS 13.0 Section 7.9

configure() {
    ./configure --prefix=/usr           \
                --enable-shared         \
                --without-ensurepip     \
                --without-static-libpython
}

build() {
    make -j${IGOS_JOBS}
}

install() {
    make DESTDIR=$IGOS install
}
