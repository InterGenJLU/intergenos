#!/bin/bash
# Man-DB 2.13.1
# LFS 13.0 Section 8.80

configure() {
    set -e
    ./configure --prefix=/usr                         \
        --docdir=/usr/share/doc/man-db-2.13.1         \
        --sysconfdir=/etc                             \
        --disable-setuid                              \
        --enable-cache-owner=bin                      \
        --with-browser=/usr/bin/lynx                  \
        --with-vgrind=/usr/bin/vgrind                 \
        --with-grap=/usr/bin/grap
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    make check
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
