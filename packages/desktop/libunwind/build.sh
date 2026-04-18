#!/bin/bash
# libunwind 1.8.1 — Call-chain determination library
# Required by: sysprof

configure() {
    ./configure --prefix=/usr \
                --disable-static
}

build() {
    make
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
