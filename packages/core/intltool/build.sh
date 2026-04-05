#!/bin/bash
# Intltool 0.51.0
# LFS 13.0 Section 8.46

configure() {
    # Fix warning caused by perl-5.22 and later
    sed -i 's:\\\${:\\\$\\{:' intltool-update.in

    ./configure --prefix=/usr
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make check
}

do_install() {
    make DESTDIR="$DESTDIR" install
    install -v -Dm644 doc/I18N-HOWTO "${DESTDIR}/usr/share/doc/intltool-0.51.0/I18N-HOWTO"
}
