#!/bin/bash
# Bc 7.1.0
# Upstream fix for GCC 15 C23 token-pasting issue (bc 7.0.3 failed)
# LFS 13.0 specifies 7.0.3 — bumped to 7.1.0 for GCC 15 compatibility

configure() {
    CC=gcc ./configure --prefix=/usr -G -O3 -r
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make test
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
