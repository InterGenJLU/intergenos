#!/bin/bash
# rpcsvc-proto 1.4.4 — RPC service protocol definitions
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --sysconfdir=/etc
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
