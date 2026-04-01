#!/bin/bash
# Findutils 4.10.0
# LFS 13.0 Section 8.64

configure() {
    ./configure --prefix=/usr \
        --localstatedir=/var/lib/locate
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
