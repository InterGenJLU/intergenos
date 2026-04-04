#!/bin/bash
# font-alias 1.0.6 — X font aliases
# BLFS 13.0

configure() {
    ./configure --prefix=/usr
                --sysconfdir=/etc \
                --localstatedir=/var \
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
