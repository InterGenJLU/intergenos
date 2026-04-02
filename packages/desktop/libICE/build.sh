#!/bin/bash
# libICE 1.1.2 — Inter-Client Exchange library
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --disable-static \
                ICE_LIBS=-lpthread
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
