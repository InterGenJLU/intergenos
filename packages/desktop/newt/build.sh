#!/bin/bash
# newt 0.52.24 — Text mode windowing toolkit
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --with-gpm-support
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
