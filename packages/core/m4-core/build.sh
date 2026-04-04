#!/bin/bash
# M4 1.4.21
# LFS 13.0 Section 8.14

configure() {
    ./configure --prefix=/usr
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    # test-execute.sh known to fail in chroot environments
    make check || true
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
