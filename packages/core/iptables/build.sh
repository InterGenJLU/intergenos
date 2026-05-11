#!/bin/bash
# iptables 1.8.12 — Linux kernel packet filtering framework
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr \
                --disable-nftables \
                --enable-libipq
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
