#!/bin/bash
# speex 1.2.1 — Audio codec designed for speech compression
# BLFS 13.0 — builds both speex and speexdsp

configure() {
    ./configure --prefix=/usr    \
                --disable-static \
                --docdir=/usr/share/doc/speex-${PKG_VERSION}
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install

    # BLFS: now build and install speexdsp from second source tarball
    cd ..
    tar -xf ${IGOS_SOURCES}/speexdsp-${PKG_VERSION}.tar.gz
    cd speexdsp-${PKG_VERSION}

    ./configure --prefix=/usr    \
                --disable-static \
                --docdir=/usr/share/doc/speexdsp-${PKG_VERSION}
    make -j${IGOS_JOBS}
    make DESTDIR="$DESTDIR" install
}
