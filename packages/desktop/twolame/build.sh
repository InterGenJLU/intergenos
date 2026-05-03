#!/bin/bash
# twolame 0.4.0 — Optimised MPEG Audio Layer 2 (MP2) encoder
# Upstream: https://www.twolame.org/

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
