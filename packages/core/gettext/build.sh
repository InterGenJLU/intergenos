#!/bin/bash
# Gettext 1.0
# LFS 13.0 Section 8.34

configure() {
    ./configure --prefix=/usr    \
        --disable-static         \
        --docdir=/usr/share/doc/gettext-1.0
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    # Some tests known to fail in chroot environments
    make check || true
}

do_install() {
    make DESTDIR="$DESTDIR" install
    chmod -v 0755 "${DESTDIR}/usr/lib/preloadable_libintl.so"
}
