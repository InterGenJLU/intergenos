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
    make check
}

install() {
    make DESTDIR="$DESTDIR" install
}
