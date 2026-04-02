#!/bin/bash
# sassc 3.6.2 — SASS CSS preprocessor compiler
# BLFS 13.0
# Note: requires separate libsass tarball in sources directory

configure() {
    # Build libsass first
    tar -xf ../libsass-3.6.6.tar.gz
    pushd libsass-3.6.6
    autoreconf -fi
    ./configure --prefix=/usr --disable-static
}

build() {
    # Build libsass
    pushd libsass-3.6.6
    make -j${IGOS_JOBS}
    popd
}

do_install() {
    # Install libsass
    pushd libsass-3.6.6
    make DESTDIR="$DESTDIR" install
    popd

    # Build and install sassc wrapper
    autoreconf -fi
    ./configure --prefix=/usr
    make -j${IGOS_JOBS}
    make DESTDIR="$DESTDIR" install
}
