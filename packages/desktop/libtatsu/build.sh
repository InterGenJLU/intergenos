#!/bin/bash
# libtatsu 1.0.5 — Apple Tatsu Signing Stuff (TSS) library
# Not in BLFS — required by libimobiledevice 1.4.0+

configure() {
    set -e
    ./configure --prefix=/usr \
                --disable-static
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
