#!/bin/bash
# ruby 3.3.6 — Ruby programming language
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --enable-shared
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
