#!/bin/bash
# Procps-ng 4.0.6
# LFS 13.0 Section 8.81

configure() {
    ./configure --prefix=/usr                           \
        --docdir=/usr/share/doc/procps-ng-4.0.6         \
        --disable-static                                \
        --disable-kill                                  \
        --enable-watch8bit                              \
        --with-systemd
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    chown -R tester .
    su tester -c "PATH=$PATH make check"
}

install() {
    make DESTDIR="$DESTDIR" install
}
