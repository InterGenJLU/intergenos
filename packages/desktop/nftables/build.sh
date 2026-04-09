#!/bin/bash
# nftables 1.1.3 — Netfilter nftables packet filtering framework
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --sysconfdir=/etc \
                --with-json \
                --disable-man-doc \
                --with-cli=readline
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
