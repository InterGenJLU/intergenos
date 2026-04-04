#!/bin/bash
# libppd 2.1.1 — PPD file handling library
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --disable-static \
                --with-cups-rundir=/run/cups \
                --enable-ppdc-utils
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
