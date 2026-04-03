#!/bin/bash
# sassc 3.6.2 — SASS CSS preprocessor compiler
# BLFS 13.0

configure() {
    # Build and install libsass first
    tar -xf "${IGOS_SOURCES}/libsass-3.6.6.tar.gz"
    cd libsass-3.6.6
    autoreconf -fi
    ./configure --prefix=/usr --disable-static
    make -j${IGOS_JOBS}
    # Install libsass to live filesystem (not DESTDIR) so sassc's
    # configure can find it via pkg-config
    make install
    cd ..

    # Now configure sassc
    autoreconf -fi
    ./configure --prefix=/usr
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
