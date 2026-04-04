#!/bin/bash
# Libffi 3.5.2
# LFS 13.0 Section 8.51

configure() {
    ./configure --prefix=/usr    \
        --disable-static         \
        --with-gcc-arch=native
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
