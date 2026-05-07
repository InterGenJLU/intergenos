#!/bin/bash
# links 2.30 — Text and graphics mode web browser
# BLFS 13.0

configure() {
    set -e
    # Fix for glibc-2.43+
    sed '/*strchr/s/cast_const_char //g' -i ftp.c

    ./configure --prefix=/usr \
                --mandir=/usr/share/man
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    install -v -d -m755 "${DESTDIR}/usr/share/doc/links-${PKG_VERSION}"
    install -v -m644 doc/links_cal/* KEYS BRAILLE_HOWTO \
        "${DESTDIR}/usr/share/doc/links-${PKG_VERSION}"
}
