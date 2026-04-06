#!/bin/bash
# bdftopcf 1.1 — BDF to PCF bitmap font converter
# Xorg application

configure() {
    ./configure --prefix=/usr
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
