#!/bin/bash
# sassc 3.6.2 — SASS CSS preprocessor compiler
# BLFS 13.0

configure() {
    set -e
    # Build and install libsass first
    tar -xf "${IGOS_SOURCES}/libsass-3.6.6.tar.gz"
    cd libsass-3.6.6
    autoreconf -fi
    ./configure --prefix=/usr --disable-static
    make -j${IGOS_JOBS}
    # Install libsass to live filesystem — must unset DESTDIR which
    # the builder exports, otherwise autotools picks it up from env
    env -u DESTDIR make install
    ldconfig
    cd ..

    # Now configure sassc
    autoreconf -fi
    ./configure --prefix=/usr
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
