#!/bin/bash
# hwdata 0.389 — Hardware identification and configuration data
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --disable-blacklist
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
