#!/bin/bash
# libtirpc 1.3.7 — Transport-Independent RPC library
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --sysconfdir=/etc \
                --disable-static \
                --disable-gssapi
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make check || true
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
