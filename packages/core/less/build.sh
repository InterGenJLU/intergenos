#!/bin/bash
# Less 692
# LFS 13.0 Section 8.43

configure() {
    ./configure --prefix=/usr --sysconfdir=/etc
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make check || true
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
