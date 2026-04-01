#!/bin/bash
# Elfutils 0.194 (Libelf only)
# LFS 13.0 Section 8.50
#
# Only libelf is built and installed from the elfutils package.

configure() {
    ./configure --prefix=/usr        \
        --disable-debuginfod         \
        --enable-libdebuginfod=dummy
}

build() {
    make -C lib -j${IGOS_JOBS}
    make -C libelf -j${IGOS_JOBS}
}

check() {
    : # Test suite fails to build with glibc-2.43+, skip
}

do_install() {
    make -C libelf DESTDIR="$DESTDIR" install
    install -vm644 config/libelf.pc "${DESTDIR}/usr/lib/pkgconfig"
    rm -f "${DESTDIR}/usr/lib/libelf.a"
}
