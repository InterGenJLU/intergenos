#!/bin/bash
# Expat 2.7.4
# LFS 13.0 Section 8.41

configure() {
    ./configure --prefix=/usr    \
        --disable-static         \
        --docdir=/usr/share/doc/expat-2.7.4
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make check
}

install() {
    make DESTDIR="$DESTDIR" install
    install -v -m644 doc/*.{html,css} "${DESTDIR}/usr/share/doc/expat-2.7.4"
}
