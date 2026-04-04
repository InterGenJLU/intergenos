#!/bin/bash
# protobuf-c 1.5.2 — C implementation of Protocol Buffers
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --disable-static
}

build() {
    make -j1
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
