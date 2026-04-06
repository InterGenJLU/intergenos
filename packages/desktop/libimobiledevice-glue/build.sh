#!/bin/bash
# libimobiledevice-glue 1.3.2 — Common code for libimobiledevice libraries
# Not in BLFS — standard autotools

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
