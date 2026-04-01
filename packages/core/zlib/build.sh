#!/bin/bash
# Zlib 1.3.2
# LFS 13.0 Section 8.6

configure() {
    ./configure --prefix=/usr
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make check
}

install() {
    make install
    rm -fv /usr/lib/libz.a
}
