#!/bin/bash
# cpio 2.15 — GNU cpio
# BLFS 13.0

configure() {
    set -e
    # GCC-15 fixes
    sed -e "/^extern int (\*xstat)/s/()/(const char * restrict,  struct stat * restrict)/" \
        -i src/extern.h
    sed -e "/^int (\*xstat)/s/()/(const char * restrict,  struct stat * restrict)/" \
        -i src/global.c

    ./configure --prefix=/usr \
                --enable-mt   \
                --with-rmt=/usr/libexec/rmt
}

build() {
    set -e
    make -j${IGOS_JOBS}
    makeinfo --html            -o doc/html      doc/cpio.texi
    makeinfo --html --no-split -o doc/cpio.html doc/cpio.texi
    makeinfo --plaintext       -o doc/cpio.txt  doc/cpio.texi
}

check() {
    set -e
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        make check
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
    install -v -m755 -d "${DESTDIR}/usr/share/doc/cpio-2.15/html"
    install -v -m644    doc/html/* \
                        "${DESTDIR}/usr/share/doc/cpio-2.15/html"
    install -v -m644    doc/cpio.{html,txt} \
                        "${DESTDIR}/usr/share/doc/cpio-2.15"
}
