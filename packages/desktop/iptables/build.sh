#!/bin/bash
# iptables 1.8.11 — Linux kernel packet filtering framework
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --disable-nftables \
                --enable-libipq
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
