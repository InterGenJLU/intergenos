#!/bin/bash
# GDBM 1.26
# LFS 13.0 Section 8.39

configure() {
    ./configure --prefix=/usr    \
        --disable-static         \
        --enable-libgdbm-compat
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make check
}

install() {
    make DESTDIR="$DESTDIR" install
}
