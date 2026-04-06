#!/bin/bash
# libtatsu 1.0.5 — Apple Tatsu Signing Stuff (TSS) library
# Not in BLFS — required by libimobiledevice 1.4.0+

configure() {
    ./configure --prefix=/usr \
                --disable-static
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
