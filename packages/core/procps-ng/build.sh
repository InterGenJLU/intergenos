#!/bin/bash
# Procps-ng 4.0.6
# LFS 13.0 Section 8.81

configure() {
    set -e
    ./configure --prefix=/usr                           \
        --docdir=/usr/share/doc/procps-ng-4.0.6         \
        --disable-static                                \
        --disable-kill                                  \
        --enable-watch8bit                              \
        --with-systemd
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    chown -R tester .
    su tester -c "PATH=$PATH make check"
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
