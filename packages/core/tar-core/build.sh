#!/bin/bash
# Tar 1.35
# LFS 13.0 Section 8.73

configure() {
    FORCE_UNSAFE_CONFIGURE=1 \
    ./configure --prefix=/usr
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make check || true
}

do_install() {
    make DESTDIR="$DESTDIR" install
    make DESTDIR="$DESTDIR" -C doc install-html docdir=/usr/share/doc/tar-1.35
}
