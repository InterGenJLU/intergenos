#!/bin/bash
# twolame 0.4.0 — Optimised MPEG Audio Layer 2 (MP2) encoder
# Upstream: https://www.twolame.org/

configure() {
    set -e
    ./configure --prefix=/usr \
                --disable-static
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
