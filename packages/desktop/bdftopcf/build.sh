#!/bin/bash
# bdftopcf 1.1 — BDF to PCF bitmap font converter
# Xorg application

configure() {
    set -e
    ./configure --prefix=/usr
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
