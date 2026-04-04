#!/bin/bash
# slang 2.3.3 — S-Lang programming library
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --sysconfdir=/etc \
                --with-readline=gnu
}

build() {
    # BLFS: slang does not support parallel build; RPATH= prevents hardcoded paths
    make -j1 RPATH=
}

do_install() {
    make DESTDIR="$DESTDIR" RPATH= install
}
