#!/bin/bash
# Make 4.4.1
# LFS 13.0 Section 8.71

configure() {
    ./configure --prefix=/usr
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    chown -R tester .
    su tester -c "PATH=$PATH make check"
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
