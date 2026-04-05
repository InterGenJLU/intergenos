#!/bin/bash
# Man-DB 2.13.1
# LFS 13.0 Section 8.80

configure() {
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
    make -j${IGOS_JOBS}
}

check() {
    make check
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
