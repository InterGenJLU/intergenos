#!/bin/bash
# Elfutils 0.194 (Libelf only)
# LFS 13.0 Section 8.50
#
# Only libelf is built and installed from the elfutils package.

configure() {
    set -e
    ./configure --prefix=/usr        \
        --disable-debuginfod         \
        --enable-libdebuginfod=dummy
}

build() {
    set -e
    make -C lib -j${IGOS_JOBS}
    make -C libelf -j${IGOS_JOBS}
}

check() {
    set -e
    : # Test suite fails to build with glibc-2.43+, skip
}

do_install() {
    set -e
    make -C libelf DESTDIR="$DESTDIR" install
    install -vDm644 config/libelf.pc "${DESTDIR}/usr/lib/pkgconfig/libelf.pc"
    rm -f "${DESTDIR}/usr/lib/libelf.a"
}
