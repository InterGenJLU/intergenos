#!/bin/bash
# libunistring 1.4.1 — Unicode string library
# BLFS 13.0

configure() {
    # Fix required by glibc-2.43+
    sed -r '/_GL_EXTERN_C/s/w?memchr|bsearch/(&)/' \
        -i $(find -name \*.in.h)

    ./configure --prefix=/usr    \
                --disable-static \
                --docdir=/usr/share/doc/libunistring-1.4.1
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
