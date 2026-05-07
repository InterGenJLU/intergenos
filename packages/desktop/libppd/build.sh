#!/bin/bash
# libppd 2.1.1 — PPD file handling library
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr \
                --disable-static \
                --with-cups-rundir=/run/cups \
                --enable-ppdc-utils
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
